cmake_minimum_required(VERSION 3.20)
project(audio_chat_device_c C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

add_library(audio_chat_device
    src/audio_chat_device.c
    src/audio_chat_stream.c
)
target_include_directories(audio_chat_device PUBLIC include)

enable_testing()
add_executable(test_audio_chat_stream tests/test_audio_chat_stream.c)
target_link_libraries(test_audio_chat_stream PRIVATE audio_chat_device)
add_test(NAME test_audio_chat_stream COMMAND test_audio_chat_stream)
