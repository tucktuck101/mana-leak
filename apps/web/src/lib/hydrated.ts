"use client";

import { useSyncExternalStore } from "react";

// `false` while the page is server-rendered and during the hydration render,
// `true` once React has taken over on the client.
//
// Why the app needs to know: the chat pages are server-rendered, so their
// controls are on screen — and behave like plain HTML — before React attaches
// any handler. A click in that window is not a no-op: "New chat" silently does
// nothing, and Send submits the composer's <form> natively, which navigates
// the browser to `/c/<id>?`. That loses the typed message and drops an
// in-flight SSE turn, which the API records as a truncated answer with
// `payload.error = {"code": "timeout", "message": "client disconnected"}`
// (contracts.md -> Streaming events -> Client disconnect). Controls that
// cannot work yet are disabled until this returns `true`.
//
// `useSyncExternalStore` rather than `useState` + `useEffect`: the server
// snapshot is the hydration-safe way to render one thing on the server and
// another after hydration, with no setState during an effect.
const subscribe = (): (() => void) => {
  return () => {};
};
const onClient = (): boolean => true;
const onServer = (): boolean => false;

export function useHydrated(): boolean {
  return useSyncExternalStore(subscribe, onClient, onServer);
}
