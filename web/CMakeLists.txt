# Wargus for the web: the Stratagus engine built with Emscripten, with this repository's data prepared for it from the
# original game.
#
#     emcmake cmake -B build -DWARGUS_ORIGINAL=<the original game>   (the top CMakeLists.txt hands over to this one)
#     cmake --build build
#
# WARGUS_ORIGINAL is the installed original game, or GOG's installer (its .exe, with its .bin next to it, named after
# it: setup_warcraft_ii.exe and setup_warcraft_ii-1.bin, say), which is unpacked the first time; after that the
# installer can be removed. WARGUS_LOCALIZED adds the game in other languages, from the INSTALL.EXE of a Battle.net
# Edition CD in that language (its texts, speech and music): a list of <language>=<INSTALL.EXE>, such as
# it=install_it.exe, converted the first time (see prepare_data.py). STRATAGUS_SOURCE is Stratagus's source
# (default: next to this repository), from its web branch (cmake/web.cmake).
#
# Writes, at the top of the build folder, Wargus.js, Wargus.wasm and Wargus.data, to be run by a web page (see
# Stratagus's cmake/web.cmake), and Wargus.png, a 256x256 icon. The data is prepared by prepare_data.py, which needs Python with Pillow, ffmpeg, oggenc,
# innoextract (for an installer), and a native C++ compiler with libpng and zlib (to build wartool).

cmake_minimum_required(VERSION 3.25)
project(wargus_web C CXX)

if(NOT EMSCRIPTEN)
	message(FATAL_ERROR "This is the web build: configure it with emcmake")
endif()

set(WARGUS_ORIGINAL "" CACHE FILEPATH "The original game: its installed folder, or GOG's installer")
set(WARGUS_LOCALIZED "" CACHE STRING "The game in other languages: a list of <language>=<CD's INSTALL.EXE>")
set(STRATAGUS_SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/../../stratagus" CACHE PATH "Stratagus's source")
find_package(Python3 REQUIRED COMPONENTS Interpreter)

# The data, prepared on every build (prepare_data.py only redoes what changed, and rewrites data/.stamp when the data
# changes, which relinks the engine). The stamp must exist when the engine is configured: see Stratagus's web.cmake.
set(WARGUS_WEB_DATA "${CMAKE_BINARY_DIR}/data")
set(localized)
foreach(entry IN LISTS WARGUS_LOCALIZED)
	list(APPEND localized --localized "${entry}")
endforeach()
file(MAKE_DIRECTORY "${WARGUS_WEB_DATA}")
if(NOT EXISTS "${WARGUS_WEB_DATA}/.stamp")
	file(TOUCH "${WARGUS_WEB_DATA}/.stamp")
endif()
add_custom_target(wargus_data
	COMMAND Python3::Interpreter "${CMAKE_CURRENT_SOURCE_DIR}/prepare_data.py" "${WARGUS_ORIGINAL}" "${WARGUS_WEB_DATA}"
	        --stratagus "${STRATAGUS_SOURCE}" --work "${CMAKE_BINARY_DIR}/work"
	        --icon "${CMAKE_BINARY_DIR}/Wargus.png"
	        ${localized}
	USES_TERMINAL
	VERBATIM
)

# The engine, named after the game
set(STRATAGUS_WEB_DATA "${WARGUS_WEB_DATA}" CACHE PATH "" FORCE)
add_subdirectory("${STRATAGUS_SOURCE}" stratagus EXCLUDE_FROM_ALL)
add_dependencies(stratagus wargus_data)
set_target_properties(stratagus PROPERTIES OUTPUT_NAME Wargus RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}")
add_custom_target(wargus ALL DEPENDS stratagus)
