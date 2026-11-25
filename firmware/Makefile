# PC build of the controller firmware (core + simulated board), used by the acceptance tests.
# The ESP32 build uses PlatformIO:  pio run -e esp32dev
CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Werror

build/controller_native: src/core/*.cpp src/core/*.h src/native/*.cpp
	mkdir -p build
	$(CXX) $(CXXFLAGS) src/core/json.cpp src/core/controller.cpp src/native/main_native.cpp -o $@

test: build/controller_native
	cd ../kiosk-agent && PL_CONFORMANCE_CMD="$(CURDIR)/build/controller_native" python3 -m pytest -q tests/test_controller_protocol.py

clean:
	rm -rf build

.PHONY: test clean
