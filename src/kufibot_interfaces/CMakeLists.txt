cmake_minimum_required(VERSION 3.8)
project(kufibot_interfaces)

find_package(ament_cmake REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/Landmark.msg"
  "msg/Detection.msg"
  "msg/DetectionArray.msg"
  "msg/TrackingTarget.msg"
  "msg/JointCommand.msg"
  "msg/VoiceState.msg"
  "msg/Transcript.msg"
  DEPENDENCIES geometry_msgs std_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
