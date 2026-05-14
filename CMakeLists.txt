cmake_minimum_required(VERSION 3.20)
project(dagcmp LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Warnings
add_compile_options(-Wall -Wextra -Wpedantic -Wno-unused-parameter)

# Release by default
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# simdjson (system)
find_package(PkgConfig REQUIRED)
pkg_check_modules(SIMDJSON REQUIRED IMPORTED_TARGET simdjson)

add_executable(dagcmp
    src/main.cpp
    src/jsonl_reader.cpp
    src/ruma_runner.cpp
    src/analyzer.cpp
    src/display.cpp
)

target_include_directories(dagcmp PRIVATE src)
target_link_libraries(dagcmp PRIVATE PkgConfig::SIMDJSON)

install(TARGETS dagcmp DESTINATION bin)
