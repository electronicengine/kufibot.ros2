cmake_minimum_required(VERSION 3.8)
project(kufibot_interfaces)

find_package(ament_cmake REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(action_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/Landmark.msg"
  "msg/Detection.msg"
  "msg/DetectionArray.msg"
  "msg/TrackingTarget.msg"
  "msg/JointCommand.msg"
  "msg/DriveCommand.msg"
  "msg/VoiceState.msg"
  "msg/Transcript.msg"
  "action/NavigateStep.action"
  "action/FollowRoute.action"
  "srv/NavigationTask.srv"
  "srv/GetObservation.srv"
  DEPENDENCIES geometry_msgs std_msgs sensor_msgs action_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
