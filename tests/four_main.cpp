#include <iostream>

void test_four_board();
void test_four_economy();
void test_four_policy();
void test_four_neural();

int main() {
    test_four_board();
    test_four_economy();
    test_four_policy();
    test_four_neural();
    std::cout << "Multiplayer board, economy, and policy tests passed.\n";
}
