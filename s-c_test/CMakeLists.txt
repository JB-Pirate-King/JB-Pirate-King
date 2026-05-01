cmake_minimum_required(VERSION 3.16)
project(ais_ids_inspector VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ──────────────────────────────────────────────
#  Snort 3 경로 (환경에 맞게 수정)
# ──────────────────────────────────────────────
set(SNORT3_DIR "/usr/local/snort3" CACHE PATH "Snort 3 설치 경로")

find_path(SNORT3_INCLUDE_DIR
    NAMES framework/inspector.h
    PATHS ${SNORT3_DIR}/include/snort
    REQUIRED
)

# ──────────────────────────────────────────────
#  ONNX Runtime
# ──────────────────────────────────────────────
find_path(ORT_INCLUDE_DIR
    NAMES onnxruntime_cxx_api.h
    PATHS /usr/local/include/onnxruntime
          /opt/onnxruntime/include
    REQUIRED
)
find_library(ORT_LIB
    NAMES onnxruntime
    PATHS /usr/local/lib /opt/onnxruntime/lib
    REQUIRED
)

# ──────────────────────────────────────────────
#  nlohmann/json
# ──────────────────────────────────────────────
find_package(nlohmann_json 3.9 REQUIRED)

# ──────────────────────────────────────────────
#  소스 파일
# ──────────────────────────────────────────────
set(PLUGIN_SOURCES
    ais_ids_inspector.cpp
    ais_ids_inspector.h
    # 기존 코드 (wx 의존성 제거 버전)
    ais_ids_core.cpp
    ais_ids_core.h
    ais_ml.cpp
    ais_ml.h
    ais_nmea_parser.cpp
    ais_nmea_parser.h
)

add_library(ais_ids_inspector SHARED ${PLUGIN_SOURCES})

target_include_directories(ais_ids_inspector PRIVATE
    ${SNORT3_INCLUDE_DIR}
    ${ORT_INCLUDE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_libraries(ais_ids_inspector PRIVATE
    ${ORT_LIB}
    nlohmann_json::nlohmann_json
)

target_compile_options(ais_ids_inspector PRIVATE
    -Wall -Wextra -O2
    -fPIC -fvisibility=hidden
)

# Snort SO 플러그인은 .so 앞에 lib 접두사 없어야 함
set_target_properties(ais_ids_inspector PROPERTIES
    PREFIX ""
    OUTPUT_NAME "ais_ids_inspector"
)

# ──────────────────────────────────────────────
#  설치
# ──────────────────────────────────────────────
install(TARGETS ais_ids_inspector
    LIBRARY DESTINATION ${SNORT3_DIR}/lib/snort/inspectors
)

install(FILES
    models/model.onnx
    models/scaler.json
    models/threshold.txt
    DESTINATION /opt/ais_ids/models
)

# ──────────────────────────────────────────────
#  snort.lua 설정 스니펫 출력
# ──────────────────────────────────────────────
message(STATUS "")
message(STATUS "== 설치 후 snort.lua에 다음을 추가하세요 ==")
message(STATUS "")
message(STATUS "  ais_ids = {")
message(STATUS "    model_dir   = '/opt/ais_ids/models/',")
message(STATUS "    ais_port    = 10110,")
message(STATUS "    rule_checks = true,")
message(STATUS "    ml_checks   = true,")
message(STATUS "  }")
message(STATUS "")
