cmake_minimum_required(VERSION 3.8)
project(jackal_custom_config)

find_package(ament_cmake REQUIRED)

install(
  PROGRAMS
    scripts/goal_mission_orchestrator.py
    scripts/goal_mission_orchestrator_test.py
    scripts/simple_record_trigger.py
  DESTINATION lib/${PROJECT_NAME}
)

install(
  DIRECTORY
    launch
    config
    rviz
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
