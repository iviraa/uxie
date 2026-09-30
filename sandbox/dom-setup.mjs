// Preloaded for React/JSX tests: a DOM must exist before any test module is imported,
// and ES imports are hoisted, so registering inside the test file is too late.
import { GlobalRegistrator } from "@happy-dom/global-registrator";

GlobalRegistrator.register();
