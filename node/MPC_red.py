#!/usr/bin/env python3

"""
Copyright 2022 Jiancheng Zhang

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import numpy as np
import pandas as pd
from casadi import *

import rospy
import rospkg
import visualization_msgs.msg
from std_msgs.msg import String
from sensor_msgs.msg import LaserScan
from ackermann_msgs.msg import AckermannDriveStamped
from nav_msgs.msg import OccupancyGrid
from visualization_msgs.msg import Marker

import math

"""
This node is a standard Model Predictive Control algorithm, it subscribes carState and publish drive command
"""


class bicycle_model():
    """
    this is the vehicle model in Python version, only used for the nonlinear solver
    """

    def __init__(self, wheelbase, friction_coeff, h_cg, l_f, l_r, cs_f, cs_r, mass, Iz, Cm1, Cm2, Cm3, B_f, C_f, D_f,
                 B_r, C_r, D_r, max_accel, max_decel, max_speed, max_steering_vel, max_steering_angle):
        self.wheelbase = wheelbase
        self.friction_coeff = friction_coeff
        self.h_cg = h_cg
        self.l_f = l_f
        self.l_r = l_r
        self.cs_f = cs_f
        self.cs_r = cs_r
        self.mass = mass
        self.Iz = Iz
        self.Cm1 = Cm1
        self.Cm2 = Cm2
        self.Cm3 = Cm3
        self.B_f = B_f
        self.C_f = C_f
        self.D_f = D_f
        self.B_r = B_r
        self.C_r = C_r
        self.D_r = D_r
        self.max_accel = max_accel
        self.max_decel = max_decel
        self.max_speed = max_speed
        self.max_steering_vel = max_steering_vel
        self.max_steering_angle = max_steering_angle

    def update_model(self, start_state, velocity, steering_angle, dt):
        """
        the only usage is in nonlinear solver, to get a new carState based on current carState and a drive command

        :param start_state: [0: x, 1: y, 2: theta, 3: velocity x, 4: velocity y, 5: steering_angle, 6: angular_velocity]
        :param velocity:
        :param steering_angle:
        :param dt:
        :return:
        """
        accelerate = self.compute_accel(velocity, start_state[3])

        start_state[3] = start_state[3] + 0.00001

        d = accelerate / 7.51
        Fx_fr = self.Cm1 * d / self.Cm2 - start_state[3] / self.Cm3

        alpha_f = -np.arctan2((start_state[5] * self.l_f + start_state[4]) , start_state[3]) + steering_angle
        alpha_r = np.arctan2((start_state[5] * self.l_r - start_state[4]) , start_state[3])

        Fyf = self.D_f * np.sin(self.C_f * np.arctan(self.B_f * alpha_f))*25
        Fyr = self.D_r * np.sin(self.C_r * np.arctan(self.B_r * alpha_r))*25

        x_next = start_state[0] + dt * (start_state[3] * np.cos(start_state[2]) - start_state[4] * np.sin(start_state[2]))
        y_next = start_state[1] + dt * (start_state[3] * np.sin(start_state[2]) + start_state[4] * np.cos(start_state[2]))
        theta_next = start_state[2] + dt * start_state[5]


        vx_next = start_state[3] + dt * ((Fx_fr * np.cos(steering_angle) - Fyf * np.sin(steering_angle) + self.mass * start_state[4] * start_state[5]) / self.mass)
        vy_next = start_state[4] + dt * ((Fyr + Fyf * np.sin(steering_angle) + Fx_fr * sin(steering_angle) - self.mass * start_state[3] * start_state[5]) / self.mass)
        yaw_next = start_state[5] + dt * ((Fyf * self.l_f * np.cos(steering_angle) - Fyr * self.l_r) / self.Iz)

        end_state = [x_next, y_next, theta_next, vx_next, vy_next, yaw_next]
        return end_state

    def compute_accel(self, desired_velocity, current_velocity):
        """

        :param desired_velocity:
        :param current_velocity:
        :return:
        """

        dif = desired_velocity - current_velocity
        kp = 2.0 * self.max_accel / self.max_speed
        return kp*dif



class NFTOCPNLP(object):
    """

    """
    def __init__(self, N, Q, R, Qf, goal, upx, lowx, bu, NonLinearBicycleModel):
        # Define variables
        self.N = N
        self.n = Q.shape[1]
        self.d = R.shape[1]
        self.upx = upx
        self.lowx = lowx
        self.bu = bu
        self.Q = Q
        self.Qf = Qf
        self.R = R
        self.goal = goal
        self.dt = dt
        self.optCost = np.inf
        self.NonLinearBicycleModel = NonLinearBicycleModel
        self.buildFTOCP()
        self.solverTime = []

    def solve(self, x0, verbose=False):
        # Set initial condition + state and input box constraints
        self.lbx = x0.tolist() + (self.lowx).tolist() * (self.N) + (-self.bu).tolist() * self.N
        self.ubx = x0.tolist() + (self.upx).tolist() * (self.N) + (self.bu).tolist() * self.N

        # Solve the NLP
        start = rospy.get_time()
        sol = self.solver(lbx=self.lbx, ubx=self.ubx, lbg=self.lbg_dyanmics, ubg=self.ubg_dyanmics)
        # rospy.loginfo(sol)
        end = rospy.get_time()
        delta = end - start
        self.solverTime.append(delta)

        # Check if the solution is feasible
        if (self.solver.stats()['success']):
            self.feasible = 1
            x = sol["x"]
            self.qcost = sol["f"]
            self.xPred = np.array(x[0:(self.N + 1) * self.n].reshape((self.n, self.N + 1))).T
            self.uPred = np.array(
                x[(self.N + 1) * self.n:((self.N + 1) * self.n + self.d * self.N)].reshape((self.d, self.N))).T
            self.mpcInput = self.uPred[0][0]

            # rospy.loginfo("xPredicted:")
            # rospy.loginfo(self.xPred)
            # rospy.loginfo("uPredicted:")
            # rospy.loginfo(self.uPred)
            # rospy.loginfo("Cost:")
            # rospy.loginfo(self.qcost)

            # rospy.loginfo("NLP Solver Time: %s%s", delta, " seconds.")
        else:
            self.xPred = np.zeros((self.N + 1, self.n))
            self.uPred = np.zeros((self.N, self.d))
            self.mpcInput = []
            self.feasible = 0
            rospy.logwarn("MPC solution Unfeasible")

        return self.uPred[1]

    def buildFTOCP(self):

        n = self.n
        d = self.d

        # Define variables
        X = SX.sym('X', n * (self.N + 1))
        U = SX.sym('U', d * self.N)

        # Define dynamic constraints
        self.constraint = []

        for i in range(self.N):
            X_next = self.NonLinearBicycleModel.update_model(X[n * i:n * (i + 1)],
                                                             U[d * i],
                                                             U[d * i + 1], dt)
            for j in range(0, self.n):
                self.constraint = vertcat(self.constraint, X_next[j] - X[n * (i + 1) + j])

        self.cost = 0
        for i in range(0, self.N):
            self.cost = self.cost + (X[n * i:n * (i + 1)] - self.goal).T @ self.Q @ (X[n * i:n * (i + 1)] - self.goal)
            self.cost = self.cost + U[d * i:d * (i + 1)].T @ self.R @ U[d * i:d * (i + 1)]

        self.cost = self.cost + (X[n * self.N:n * (self.N + 1)] - self.goal).T @ self.Qf @ (
                X[n * self.N:n * (self.N + 1)] - self.goal)

        # Standard NLP
        opts = {"verbose": False, "ipopt.print_level": 0, "print_time": 0}
        nlp = {'x': vertcat(X, U), 'f': self.cost, 'g': self.constraint}
        self.solver = nlpsol('solver', 'ipopt', nlp, opts)

        # Set lower bound of inequality constraint to zero to force n*N state dynamics
        self.lbg_dyanmics = [0] * (n * self.N)
        self.ubg_dyanmics = [0] * (n * self.N)


# [0: x, 1: y, 2: theta, 3: velocity x, 4: velocity y, 5: steering_angle, 6: angular_velocity, 7: slip_angle]
x_cl_nlp_dy = np.zeros(8)
carState_topic = ""
scan_topic_red = ""
MPC_drive_topic = ""


def carState_callback(data):
    global x_cl_nlp_dy
    x_cl_nlp_dy = np.asarray(data.data.split(","), dtype=float)
    # x_cl_nlp_dy[2] = round_to_minusPI_PI(x_cl_nlp_dy[2])


def round_to_minusPI_PI(x):
    x = math.fmod(x, 2*math.pi)
    if x>math.pi:
        x -= 2*math.pi
    if x<-math.pi:
        x += 2*math.pi
    return x

if __name__ == '__main__':
    # anonymous=True flag means that rospy will choose a unique name for our 'listener' node so that multiple
    # listeners can run simultaneously.
    rospy.init_node("MPC_red", anonymous=True)
    # need to add ~ before key name
    MPC_drive_topic = rospy.get_param("~MPC_drive_topic")
    scan_topic_red = rospy.get_param("~scan_topic_red")
    carState_topic = rospy.get_param("~carState_topic_red")

    wheelbase = rospy.get_param("~wheelbase")
    friction_coeff = rospy.get_param("~friction_coeff")
    h_cg = rospy.get_param("~height_cg")
    l_f = rospy.get_param("~l_cg2front")
    l_r = rospy.get_param("~l_cg2rear")
    cs_f = rospy.get_param("~C_S_front")
    cs_r = rospy.get_param("~C_S_rear")
    mass = rospy.get_param("~mass")
    Iz = rospy.get_param("~moment_inertia")
    Cm1 = rospy.get_param("~empirical_drivetrain_parameters_1")
    Cm2 = rospy.get_param("~empirical_drivetrain_parameters_2")
    Cm3 = rospy.get_param("~empirical_drivetrain_parameters_3")
    B_f = rospy.get_param("~empirical_Pacejka_parameters_B_f")
    C_f = rospy.get_param("~empirical_Pacejka_parameters_C_f")
    D_f = rospy.get_param("~empirical_Pacejka_parameters_D_f")
    B_r = rospy.get_param("~empirical_Pacejka_parameters_B_r")
    C_r = rospy.get_param("~empirical_Pacejka_parameters_C_r")
    D_r = rospy.get_param("~empirical_Pacejka_parameters_D_r")
    max_accel = rospy.get_param("~max_accel")
    max_decel = rospy.get_param("~max_decel")
    max_speed = rospy.get_param("~max_speed")
    max_steering_vel = rospy.get_param("~max_steering_vel")
    max_steering_angle = rospy.get_param("~max_steering_angle")
    map_name = rospy.get_param("~map_name")
    map_topic = rospy.get_param("~map_topic")
    map_frame = rospy.get_param("~map_frame")
    field_of_view = rospy.get_param("~scan_field_of_view")
    scan_beams = rospy.get_param("~scan_beams")
    goal_path = rospy.get_param("~mpc_goal_path")


    dynamic_model = bicycle_model(wheelbase, friction_coeff, h_cg, l_f, l_r, cs_f, cs_r, mass, Iz, Cm1, Cm2, Cm3, B_f,
                              C_f, D_f,
                              B_r, C_r, D_r, max_accel, max_decel, max_speed, max_steering_vel, max_steering_angle)
    # https://wiki.ros.org/Packages#Client_Library_Support
    # get an instance of RosPack with the default search paths
    rospack = rospkg.RosPack()


    map_msg = OccupancyGrid
    map_ptr = rospy.wait_for_message(map_topic, OccupancyGrid)
    if map_ptr is not None:
        map_msg = map_ptr
    map_width = map_msg.info.width
    map_height = map_msg.info.height
    map_origin_x = map_msg.info.origin.position.x
    map_origin_y = map_msg.info.origin.position.y
    map_resolution = map_msg.info.resolution

    # weight_x = 6
    # weight_y = 6
    # bias_x = 0
    # bias_y = -65.4
    weight_x = 3
    weight_y = 3
    bias_x = 0
    bias_y = -60


    reference_line_raw = pd.read_csv(rospack.get_path("f1tenth_simulator") + "/maps/" + map_name + "_minTime.csv", sep=";")
    size = reference_line_raw.shape[0]
    reference_line_x_y_theta = pd.DataFrame()
    reference_line_x_y_theta['x'] = reference_line_raw.iloc[:, 1] * weight_x * map_resolution + map_origin_x + bias_x
    reference_line_x_y_theta['y'] = - (reference_line_raw.iloc[:, 2] * weight_y * map_resolution - map_origin_y + bias_y)

    reference_line_x_y_theta['theta'] = -(reference_line_raw.iloc[:, 3] + math.pi/2)
    reference_line_x_y_theta['theta'] = reference_line_x_y_theta['theta'].map(round_to_minusPI_PI)

    delta_theta = []
    original = reference_line_x_y_theta.iloc[0, 2]
    flag_turned = False
    for index in range(size):
        if index!=0:
            diff = reference_line_x_y_theta.iloc[index, 2] - original

            if original*reference_line_x_y_theta.iloc[index, 2]<=-1:
                flag_turned = True

            original = reference_line_x_y_theta.iloc[index, 2]
            if not flag_turned:
                delta_theta.append(diff)

            if flag_turned and reference_line_x_y_theta.iloc[index, 2]<-3 and reference_line_x_y_theta.iloc[index-1, 2]>0:
                delta_theta.append(-(math.fabs(reference_line_x_y_theta.iloc[index, 2])+
                                     math.fabs(reference_line_x_y_theta.iloc[index-1, 2])-
                                     2*math.pi))

                flag_turned = False
            elif flag_turned and reference_line_x_y_theta.iloc[index, 2]>3 and reference_line_x_y_theta.iloc[index-1, 2]<0:
                delta_theta.append(math.fabs(reference_line_x_y_theta.iloc[index, 2])+ \
                                   math.fabs(reference_line_x_y_theta.iloc[index-1, 2])- \
                                   2*math.pi)

                flag_turned = False

    delta_theta.append(0)
    reference_line_x_y_theta['delta_theta'] = delta_theta


    rospy.Subscriber(carState_topic, String, carState_callback)
    drive_pub_red = rospy.Publisher(MPC_drive_topic, AckermannDriveStamped, queue_size=10)
    goal_path_pub = rospy.Publisher(goal_path, Marker, queue_size=10)
    rospy.wait_for_message(scan_topic_red, LaserScan)
    # Australia: 15
    # Shanghai 11
    # Gulf 11
    N = 10

    n_dy = 6
    d = 2

    dt = 0.01
    # sys_dy = systemdy(x0_dy, dt)


    maxTime = 2


    start = 0
    # Australia: 15
    # Shanghai 2
    # Gulf 3
    bias_index = 2


    x0_dy = x_cl_nlp_dy
    current_x = x0_dy[0]
    current_y = x0_dy[1]
    min_temp = 0x3F3F3F3F
    rospy.loginfo(current_x)
    rospy.loginfo(current_y)
    for index in range(size):
        distance_to_current = pow(reference_line_x_y_theta.iloc[index, 0] - current_x, 2) + pow(reference_line_x_y_theta.iloc[index, 1] - current_y, 2)
        if distance_to_current < min_temp:
            min_temp = distance_to_current
            start = index

    pointer = start + bias_index
    goal_theta = reference_line_x_y_theta.iloc[pointer, 2] + 2*math.pi

    # initial state
    while not rospy.is_shutdown():
        x0_dy = x_cl_nlp_dy
        current_x = x0_dy[0]
        current_y = x0_dy[1]
        current_theta = x0_dy[2]
        # binary search, can only work in reference line direction i.e. from small index to large index

        # this is to fix a corner case that the position of the car is between |size-2|-car-|size-1 aka 0|--|1|, but closer to |size-1 aka 0| and when the start is -1
        # Before go into binary search, the start is -1, the end is size-2, then distance_to_start < distance_to_end, the end will be the mid
        # Until the start is -1, the end is 0. Binary search should stop here, since size-1 is 0
        # But in this time, distance_to_start == distance_to_end, hence, start will be 1, because mid=int(-1 / 2)=0, start = mid+1=1
        # But our car is at index size-1 (0), not index 1
        # this will lead to the start become size-2 in next binary search, because car is closer to size-2 not 1,
        # which makes the pointer iterating from 1 (previous goal index) to size-2 (current goal index), and has wrong theta value
        # And the start will become -1 again, and go over the whole wrong search again
        # if we set the start=0 when start==-1 before the searching, then the searching will stop when the end is 0
        if start == -1:
            start = 0
        # -2 because first and last row in minTime is same
        end = size - 2
        while start < end:
            mid = int((start + end) / 2)
            distance_to_start = pow(reference_line_x_y_theta.iloc[start, 0] - current_x, 2) + pow(
                reference_line_x_y_theta.iloc[start, 1] - current_y, 2)
            distance_to_end = pow(reference_line_x_y_theta.iloc[end, 0] - current_x, 2) + pow(
                reference_line_x_y_theta.iloc[end, 1] - current_y, 2)
            if distance_to_start < distance_to_end:
                end = mid
            else:
                start = mid + 1

        if start == size-2:
            start = -1
        goal_index = (start + bias_index) % size
        goal_x = reference_line_x_y_theta.iloc[goal_index, 0]
        goal_y = reference_line_x_y_theta.iloc[goal_index, 1]


        # prefix doesn't work here, because when the pointer on the left hand side of 0 and the goal_index on the right hand side of 0,
        # the result is pointless.
        # prefix only works in one run i.e. 0 -> size, not x -> size -> x
        # Besides, there is no large performance difference between prefix and sum all delta in a while loop,
        # because every time updating the goal_index, the goal_index will increase few points
        while pointer != goal_index:
            rospy.loginfo("pointer " + str(pointer))
            goal_theta += reference_line_x_y_theta.iloc[pointer, 3]
            pointer += 1
            pointer = pointer % size


        # rospy.loginfo("start  "+str(start))
        # rospy.loginfo("goalx "+str(goal_x))
        # rospy.loginfo("goaly "+str(goal_y))
        # rospy.loginfo("goalt  "+str(goal_theta))
        rospy.loginfo("goali "+str(goal_index))
        # rospy.loginfo("cx "+str(current_x))
        # rospy.loginfo("cy "+str(current_y))
        # rospy.loginfo("ct "+str(current_theta))

        goal_msg = Marker()
        goal_msg.header.frame_id = map_frame
        goal_msg.header.stamp = rospy.Time.now()
        goal_msg.ns = "goal"
        goal_msg.id = 1
        goal_msg.type = visualization_msgs.msg.Marker.CUBE
        goal_msg.action = visualization_msgs.msg.Marker.MODIFY
        goal_msg.pose.position.x = goal_x
        goal_msg.pose.position.y = goal_y
        goal_msg.pose.position.z = 0

        goal_msg.scale.x = 0.1
        goal_msg.scale.y = 0.1
        goal_msg.scale.z = 0.1
        goal_msg.color.a = 1.0
        goal_msg.color.r = 0.3
        goal_msg.color.g = 0.3
        goal_msg.color.b = 0.7
        goal_path_pub.publish(goal_msg)
        # goals
        xRef_dy = [goal_x, goal_y, goal_theta, 100, 0, 0]

        # for loss function
        # when overshooting, change R
        R = 5 * np.eye(d)
        Q_dy = 0 * np.eye(n_dy)
        # Qf_dy = 1000*np.eye(n_dy)
        # increase cost to solve deviation
        # Australia [50000.0, 50000.0, 50000.0, 130.0, 0.0, 0.0]
        # Shanghai [80000.0, 80000.0, 50000.0, 140.0, 0.0, 0.0]
        # Gulf [80000.0, 80000.0, 50000.0, 125.0, 0.0, 0.0]
        Qf_dy = np.diag([40000.0, 40000.0, 90000.0, 130.0, 0.0, 0.0])


        # car state constrains
        upx_dy = np.array([10000, 10000, 10000, 100, 50, 50])
        lowx_dy = np.array([-10000, -10000, -10000, 0, -50, -50])
        # input constrains
        # Australia 1
        # Shanghai 1.3
        # Gulf 1.3
        bu = np.array([max_speed, max_steering_angle+1.4])


        ## Solving the problem
        nlp_kinematic = NFTOCPNLP(N, Q_dy, R, Qf_dy, xRef_dy, upx_dy, lowx_dy, bu, dynamic_model)

        xPredNLP_dy = []
        uPredNLP_dy = []
        CostSolved_dy = []
        for t in range(0, maxTime):
            # latest state
            xt_dy = x_cl_nlp_dy[0:6]
            xt_dy[5] = x_cl_nlp_dy[6]
            # if xt_dy[3] < thresh:
            ut_dy = nlp_kinematic.solve(xt_dy)
            # xPredNLP_dy.append()
            # uPredNLP_dy.append(nlp_kinematic.uPred)
            # CostSolved_dy.append(nlp_kinematic.qcost)

            # rospy.loginfo(ut_dy[0])
            ack_msg = AckermannDriveStamped()
            ack_msg.header.stamp = rospy.Time.now()
            ack_msg.drive.steering_angle = ut_dy[1]
            ack_msg.drive.speed = ut_dy[0]
            drive_pub_red.publish(ack_msg)
