# generated from ament/cmake/core/templates/nameConfig.cmake.in

# prevent multiple inclusion
if(_jackal_custom_config_CONFIG_INCLUDED)
  # ensure to keep the found flag the same
  if(NOT DEFINED jackal_custom_config_FOUND)
    # explicitly set it to FALSE, otherwise CMake will set it to TRUE
    set(jackal_custom_config_FOUND FALSE)
  elseif(NOT jackal_custom_config_FOUND)
    # use separate condition to avoid uninitialized variable warning
    set(jackal_custom_config_FOUND FALSE)
  endif()
  return()
endif()
set(_jackal_custom_config_CONFIG_INCLUDED TRUE)

# output package information
if(NOT jackal_custom_config_FIND_QUIETLY)
  message(STATUS "Found jackal_custom_config: 0.0.0 (${jackal_custom_config_DIR})")
endif()

# warn when using a deprecated package
if(NOT "" STREQUAL "")
  set(_msg "Package 'jackal_custom_config' is deprecated")
  # append custom deprecation text if available
  if(NOT "" STREQUAL "TRUE")
    set(_msg "${_msg} ()")
  endif()
  # optionally quiet the deprecation message
  if(NOT ${jackal_custom_config_DEPRECATED_QUIET})
    message(DEPRECATION "${_msg}")
  endif()
endif()

# flag package as ament-based to distinguish it after being find_package()-ed
set(jackal_custom_config_FOUND_AMENT_PACKAGE TRUE)

# include all config extra files
set(_extras "")
foreach(_extra ${_extras})
  include("${jackal_custom_config_DIR}/${_extra}")
endforeach()
