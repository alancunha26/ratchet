# library

Packages consumed by other code: shared packages in a monorepo, SDKs, crates, modules.

## Run
No server. The tests and the public API are the product.

## Exercise
1. Read the public API surface the task changed (exports, types, docs).
2. Write a tiny consumer under `{{scratch}}` that imports the package the way users do, call the changed API, and run it.
3. Confirm the dependents still pass: `{{R}} verify` covers them because it follows dependencies.
4. For a public package, flag any breaking change that isn't stated in the task: a signature changed, an export removed, a behaviour changed.

## Evidence
- The consumer snippet and its output.
- A test that would fail without the change.

## Good criteria
- "`parseMoney('1.005')` returns 1.01 (round half up), and throws `InvalidMoney` on 'abc'. Both are covered by tests."
