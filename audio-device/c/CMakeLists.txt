cmake_minimum_required(VERSION 3.20)
project(realtime_agent_device_c C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

add_library(realtime_agent_device
    src/realtime_agent_device.c
    src/realtime_agent_stream.c
)
target_include_directories(realtime_agent_device PUBLIC include)

enable_testing()
add_executable(test_realtime_agent_stream tests/test_realtime_agent_stream.c)
target_link_libraries(test_realtime_agent_stream PRIVATE realtime_agent_device)
add_test(NAME test_realtime_agent_stream COMMAND test_realtime_agent_stream)
