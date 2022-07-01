#include <cmath>
#include <ros/ros.h>
#include "f1tenth_simulator/car_state.hpp"
#include "f1tenth_simulator/vehicle_model.hpp"
#include <iostream>

using namespace racecar_simulator;

CarState STKinematics::update(CarState start, double desired_speed, double desired_steer_ang, CarParams p, double dt) {

    double thresh = .5; // cut off to avoid singular behavior
    double err = .03; // deadband to avoid flip flop
    if (!start.st_dyn)
        thresh += err;

    double accel = compute_accel(start, desired_speed, p);
    double steer_angle_vel = compute_steer_vel(start, desired_steer_ang, p);

    // if velocity is low or negative, use normal Kinematic Single Track dynamics
    if (start.velocity_x < thresh) {
        return update_k(start, accel, steer_angle_vel, p, dt);
    }
    // force from engine
    double d = accel / 7.51;
    double Fx_fr = p.Cm1 * d / p.Cm2 - start.velocity_x / p.Cm3;
    // slip angle
    double alpha_f = -std::atan((start.angular_velocity * p.l_f + start.velocity_y) / start.velocity_x) + start.steer_angle;
    double alpha_r = std::atan((start.angular_velocity * p.l_r - start.velocity_y) / start.velocity_x);

    // lateral force
    double Fy_f = p.D_f * std::sin(p.C_f * std::atan(p.B_f * alpha_f))*25;
    double Fy_r = p.D_r * std::sin(p.C_r * std::atan(p.B_r * alpha_r))*25;
    // classic dynamic bicycle model
    double x_dot = start.velocity_x * std::cos(start.theta) - start.velocity_y * std::sin(start.theta);
    double y_dot = start.velocity_x * std::sin(start.theta) + start.velocity_y * std::sin(start.theta);
    double theta_dot = start.angular_velocity;
    double velocity_x_dot = (Fx_fr * std::cos(start.steer_angle) - Fy_f * std::sin(start.steer_angle) + p.mass * start.velocity_y * start.angular_velocity) / p.mass;
    double velocity_y_dot = (Fy_r + Fy_f * std::sin(start.steer_angle) + Fx_fr * std::sin(start.steer_angle) -p.mass * start.velocity_x * start.angular_velocity) / p.mass;
    double angular_velocity_dot = (Fy_f * p.l_f* std::cos(start.steer_angle)  - Fy_r * p.l_r) / p.Iz;

    CarState end;
    end.x = start.x + dt * x_dot;
    end.y = start.y + dt * y_dot;
    end.theta = start.theta + dt * theta_dot;
    end.velocity_x = start.velocity_x + dt * velocity_x_dot;
    end.velocity_y = start.velocity_y + dt * velocity_y_dot;
    end.steer_angle = start.steer_angle + dt * steer_angle_vel;
    // we don't want the car turning very fast
    if (std::abs(start.steer_angle) < 0.005 and std::abs(start.angular_velocity + dt * angular_velocity_dot) < 1){
        end.angular_velocity = 0;
    }else {
        end.angular_velocity = std::min(std::max(-2.0, start.angular_velocity + dt * angular_velocity_dot), 2.0);
    }

    end.slip_angle = alpha_f;
    end.st_dyn = true;

// Implementation based off of Single Track Dynamics defined in CommonRoad: Vehicle Models
// https://gitlab.lrz.de/tum-cps/commonroad-vehicle-models/blob/master/vehicleModels_commonRoad.pdf
    /*
    double g = 9.81; // m/s^2

    // compute first derivatives of state
    double x_dot = start.velocity * std::cos(start.theta + start.slip_angle);
    double y_dot = start.velocity * std::sin(start.theta + start.slip_angle);
    double v_dot = accel;
    double steer_angle_dot = steer_angle_vel;
    double theta_dot = start.angular_velocity;

    // for eases of next two calculations
    double rear_val = g * p.l_r - accel * p.h_cg;
    double front_val = g * p.l_f + accel * p.h_cg;

    // in case velocity is 0
    double vel_ratio, first_term;
    if (start.velocity == 0) {
        vel_ratio = 0;
        first_term = 0;
    }
    else {
        vel_ratio = start.angular_velocity / start.velocity;
        first_term = p.friction_coeff / (start.velocity * (p.l_r + p.l_f));
    }

    double theta_double_dot = (p.friction_coeff * p.mass / (p.I_z * p.wheelbase)) *
            (p.l_f * p.cs_f * start.steer_angle * (rear_val) +
             start.slip_angle * (p.l_r * p.cs_r * (front_val) - p.l_f * p.cs_f * (rear_val)) -
             vel_ratio * (std::pow(p.l_f, 2) * p.cs_f * (rear_val) + std::pow(p.l_r, 2) * p.cs_r * (front_val)));\

    double slip_angle_dot = (first_term) *
            (p.cs_f * start.steer_angle * (rear_val) -
             start.slip_angle * (p.cs_r * (front_val) + p.cs_f * (rear_val)) +
             vel_ratio * (p.cs_r * p.l_r * (front_val) - p.cs_f * p.l_f * (rear_val))) -
            start.angular_velocity;


    CarState end;
    // update state
    end.x = start.x + x_dot * dt;
    end.y = start.y + y_dot * dt;
    end.theta = start.theta + theta_dot * dt;
    end.velocity = start.velocity + v_dot * dt;
    end.steer_angle = start.steer_angle + steer_angle_dot * dt;
    end.angular_velocity = start.angular_velocity + theta_double_dot * dt;
    end.slip_angle = start.slip_angle + slip_angle_dot * dt;
    end.st_dyn = true;
    */
    return end;
}

CarState STKinematics::update_k(
        const CarState start,
        double accel,
        double steer_angle_vel,
        CarParams p,
        double dt) {

    CarState end;

    // compute first derivatives of state
    double x_dot = start.velocity_x * std::cos(start.theta);
    double y_dot = start.velocity_x * std::sin(start.theta);
    double v_dot = accel;
    double steer_angle_dot = steer_angle_vel;
    double theta_dot = start.velocity_x / p.wheelbase * std::tan(start.steer_angle);
    double theta_double_dot = accel / p.wheelbase * std::tan(start.steer_angle) +
            start.velocity_x * steer_angle_vel / (p.wheelbase * std::pow(std::cos(start.steer_angle), 2));
    double slip_angle_dot = 0;

    // update state
    end.x = start.x + x_dot * dt;
    end.y = start.y + y_dot * dt;
    end.theta = start.theta + theta_dot * dt;
    end.velocity_x = std::min(std::max(start.velocity_x + v_dot * dt, -2.0), 2.0);
    end.velocity_y = 0;
    end.steer_angle = start.steer_angle + steer_angle_dot * dt;
    end.angular_velocity = 0; //start.angular_velocity + theta_double_dot * dt;
    end.slip_angle = 0; //start.slip_angle + slip_angle_dot * dt;
    end.st_dyn = false;


    return end;

}

double STKinematics::compute_accel(CarState & state, double desired_speed, CarParams & p) {
    // get difference between current and desired
    double dif = (desired_speed - state.velocity_x);

    if (state.velocity_x > 0) {
        if (dif > 0) {
            // accelerate
            double kp = 2.0 * p.max_accel / p.max_speed;
            return set_accel(kp * dif, p);
        } else {
            // brake
            return -p.max_decel;
        }
    } else if (state.velocity_x < 0) {
        if (dif > 0) {
            // brake
            return p.max_decel;

        } else {
            // accelerate
            double kp = 2.0 * p.max_accel / p.max_speed;
            return set_accel(kp * dif, p);
        }
    } else {
        // zero speed, accel either way
        double kp = 2.0 * p.max_accel / p.max_speed;
        return set_accel(kp * dif, p);
    }
}

double STKinematics::set_accel(double accel_, CarParams & p) {
    return std::min(std::max(accel_, -p.max_accel), p.max_accel);
}

double STKinematics::compute_steer_vel(CarState & state, double desired_steer_ang, CarParams & p) {
    // get difference between current and desired
    double dif = (desired_steer_ang - state.steer_angle);

    // calculate velocity
    double steer_vel;
    if (std::abs(dif) > .0001)  // if the difference is not trivial
        steer_vel = dif / std::abs(dif) * p.max_steering_vel;
    else {
        steer_vel = 0;
    }

    return set_steer_angle_vel(steer_vel, p);
}

double STKinematics::set_steer_angle_vel(double steer_angle_vel_, CarParams & p) {
    return std::min(std::max(steer_angle_vel_, -p.max_steering_vel), p.max_steering_vel);
}
