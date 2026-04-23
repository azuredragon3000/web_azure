// C++ counter — compiled to WebAssembly (WASM) and inlined in index.html
// Equivalent logic running in the browser:

static int counter = 0;

int increment() {
    return ++counter;
}
