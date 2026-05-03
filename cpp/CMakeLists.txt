cmake_minimum_required(VERSION 3.20)
project(bulk_upload CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

include(FetchContent)

FetchContent_Declare(
    simdjson
    GIT_REPOSITORY https://github.com/simdjson/simdjson.git
    GIT_TAG        v3.10.1
    GIT_SHALLOW    TRUE
)

FetchContent_Declare(
    httplib
    GIT_REPOSITORY https://github.com/yhirose/cpp-httplib.git
    GIT_TAG        v0.16.3
    GIT_SHALLOW    TRUE
)

FetchContent_MakeAvailable(simdjson httplib)

add_executable(bulk_upload src/main.cpp)
target_link_libraries(bulk_upload PRIVATE simdjson::simdjson httplib::httplib)

target_compile_options(bulk_upload PRIVATE -Wall -Wextra -Wpedantic)