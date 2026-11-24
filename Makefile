CXX ?= c++
CXXFLAGS ?= -O3 -std=c++17 -Wall -Wextra -Wpedantic
PYTHON ?= $(if $(filter Darwin,$(shell uname -s)),/usr/bin/python3,python3)
CORE = src/board.cpp src/economy.cpp src/policy.cpp src/holdout.cpp src/search.cpp src/neural_value.cpp src/neural_policy.cpp src/neural_trade.cpp src/response.cpp src/opponent.cpp src/adaptive_policy.cpp src/robust_policy.cpp src/pressure_policy.cpp src/deal_value.cpp src/bundle_trade.cpp
HEADERS = $(wildcard src/*.hpp)
FOUR = src/four/board.cpp src/four/economy.cpp src/four/policy.cpp src/four/neural.cpp
MULTI_HEADERS = $(wildcard src/four/*.hpp)
MULTI_TESTS = tests/four_main.cpp tests/four_board.cpp tests/four_economy.cpp tests/four_policy.cpp tests/four_neural.cpp
TESTS = tests/main.cpp tests/board.cpp tests/economy.cpp tests/policy.cpp tests/search.cpp tests/neural_policy.cpp tests/neural_trade.cpp tests/adaptive_policy.cpp tests/bundle_trade.cpp tests/robust_policy.cpp

all: build/libmonopoly.so

build:
	mkdir -p build

build/libmonopoly_four.so: $(CORE) $(FOUR) src/four/benchmark.cpp $(MULTI_HEADERS) src/evaluation/policy.hpp $(HEADERS) | build
	$(CXX) $(CXXFLAGS) -fPIC -shared $(CORE) $(FOUR) src/four/benchmark.cpp -o $@

build/four_tests: $(CORE) $(FOUR) $(MULTI_HEADERS) $(HEADERS) $(MULTI_TESTS) | build
	$(CXX) $(CXXFLAGS) -Isrc $(CORE) $(FOUR) $(MULTI_TESTS) -o $@

build/libmonopoly.so: $(CORE) src/bindings.cpp src/bundle_match.cpp src/bundles/verified.cpp src/bundles/verified.hpp $(HEADERS) | build
	$(CXX) $(CXXFLAGS) -fPIC -shared $(CORE) src/bindings.cpp src/bundle_match.cpp src/bundles/verified.cpp -o $@

build/rule_tests: $(CORE) $(TESTS) $(HEADERS) | build
	$(CXX) $(CXXFLAGS) -Isrc $(CORE) $(TESTS) -o $@

test: all build/rule_tests build/four_tests
	./build/rule_tests
	./build/four_tests
	./monopoly check
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py'

test-http: all
	$(PYTHON) tests/test_http_game.py

.PHONY: all test test-http

build/response_data: $(CORE) src/response_data.cpp $(HEADERS) | build
	$(CXX) $(CXXFLAGS) $(CORE) src/response_data.cpp -o $@

build/adaptive_bench: $(CORE) src/adaptive_bench.cpp $(HEADERS) | build
	$(CXX) $(CXXFLAGS) $(CORE) src/adaptive_bench.cpp -o $@

build/opponent_data: $(CORE) src/opponent_data.cpp $(HEADERS) | build
	$(CXX) $(CXXFLAGS) $(CORE) src/opponent_data.cpp -o $@

build/league_bench: $(CORE) src/league_bench.cpp src/adaptive_bench.cpp src/resilience_arena.cpp $(HEADERS) | build
	$(CXX) $(CXXFLAGS) $(CORE) src/league_bench.cpp -o $@

build/deal_data: $(CORE) src/deal_data.cpp $(HEADERS) | build
	$(CXX) $(CXXFLAGS) $(CORE) src/deal_data.cpp -o $@
