import { useSyncExternalStore } from "react";
import { signal, subscribeSignal } from "./signal";

/** The signal (P5.17b), kept current in a component. */
export const useSignal = () => useSyncExternalStore(subscribeSignal, signal);
