cmake_minimum_required(VERSION 3.16)

project(realtime_agent_device_c C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

add_library(realtime_agent_device_c
    src/ra_client.c
    src/ra_diagnostics.c
    src/ra_event.c
    src/ra_log.c
    src/ra_speaker_buffer.c
    src/ra_stream_chunk.c
)

target_include_directories(realtime_agent_device_c
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_options(realtime_agent_device_c PRIVATE
    -Wall
    -Wextra
    -Wpedantic
)

enable_testing()

function(ra_add_test name source)
    add_executable(${name} ${source})
    target_link_libraries(${name} PRIVATE realtime_agent_device_c)
    target_compile_options(${name} PRIVATE -Wall -Wextra -Wpedantic)
    add_test(NAME ${name} COMMAND ${name})
endfunction()

ra_add_test(test_event_codec tests/test_event_codec.c)
ra_add_test(test_stream_chunk_codec tests/test_stream_chunk_codec.c)
ra_add_test(test_speaker_buffer tests/test_speaker_buffer.c)
ra_add_test(test_client_state_machine tests/test_client_state_machine.c)
