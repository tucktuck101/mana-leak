import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import type { ConversationDetail, MessageOut } from "@/lib/contracts";

// Browser regression test for the intermittent first-turn abort in a new chat
// (`docs/prds/M1-walking-skeleton.md` -> AC-10, AC-12). Drives the real
// Compose stack with a real Chromium:
//
//   open / -> New chat -> type -> Send -> wait for the turn to end
//          -> type again -> Send -> wait for the turn to end
//
// and then checks the server's own record of the conversation.
//
// The bug this guards against is a click that lands in the hydration window.
// The page is server-rendered, so its controls exist — and a click on them
// works like plain HTML — before React attaches any handler: "New chat" does
// nothing at all, and Send submits the composer's <form> natively, which
// navigates the browser to `/c/<id>?`. That navigation throws away the typed
// message and drops an in-flight SSE turn, which the API records as a
// truncated answer with
// `payload.error = {"code": "timeout", "message": "client disconnected"}`.
//
// Both tests therefore open pages with `waitUntil: "commit"` (the user clicks
// as soon as the page is on screen, not when the last byte has loaded) and
// hold the client bundle back, so the hydration window is open on every run
// instead of only on a cold or slow load. The flow itself is unchanged: every
// click is made as soon as the control is actionable.

const ITERATIONS = Number(process.env.BROWSER_TEST_ITERATIONS ?? 10);
const BUNDLE_DELAY_MS = Number(process.env.BROWSER_TEST_BUNDLE_DELAY_MS ?? 1500);
// A turn streams a real model answer; the API's own deadline is 120 s.
const TURN_TIMEOUT_MS = 150_000;

const FIRST_MESSAGE = "Explain the Commander format in four sentences.";
const SECOND_MESSAGE = "Reply with just the word OK.";

// Each iteration is its own test with its own browser context and its own
// conversation: an intermittent failure must show up as "3 of 10 failed",
// not stop the run at the first one.

for (let iteration = 1; iteration <= ITERATIONS; iteration++) {
  test(`new chat, two turns (${iteration}/${ITERATIONS})`, async ({ page, request }) => {
    await delayClientBundle(page);

    await page.goto("/", { waitUntil: "commit" });
    await page.getByRole("button", { name: "New chat" }).click();
    // A click swallowed by the hydration window leaves the browser on `/`
    // forever; no need to burn the whole test timeout on it.
    await page.waitForURL(/\/c\/[0-9a-f-]{36}/, { timeout: 30_000 });
    const conversationId = /\/c\/([0-9a-f-]{36})/.exec(page.url())![1];
    await markDocument(page);

    await sendAndWait(page, FIRST_MESSAGE);
    await sendAndWait(page, SECOND_MESSAGE);

    const messages = await persistedMessages(request, conversationId);
    expectNothingCutOff(messages);
    expect(
      messages.filter((m) => m.role === "user").map((m) => m.content),
      "persisted user messages",
    ).toEqual([FIRST_MESSAGE, SECOND_MESSAGE]);
    await expectAnswersRenderedInFull(page, messages);
    expect(await isSameDocument(page), "the chat page was never reloaded").toBe(true);
  });
}

// The reported failure happened on a conversation page that had been loaded as
// a document (a reload, or a link opened directly — the AC-12 demo reloads
// mid-conversation and carries on). That is the only way to type into a
// composer that React has not hydrated yet.
test("existing conversation, Send clicked before hydration", async ({ page, request }) => {
  await delayClientBundle(page);
  const created = await request.post("/api/conversations", { data: {} });
  expect(created.status(), "POST /conversations").toBe(201);
  const { id } = (await created.json()) as { id: string };

  await page.goto(`/c/${id}`, { waitUntil: "commit" });
  await markDocument(page);
  await sendAndWait(page, FIRST_MESSAGE);

  const messages = await persistedMessages(request, id);
  expectNothingCutOff(messages);
  expect(
    messages.filter((m) => m.role === "user").map((m) => m.content),
    "persisted user messages",
  ).toEqual([FIRST_MESSAGE]);
  await expectAnswersRenderedInFull(page, messages);
  expect(await isSameDocument(page), "the chat page was never reloaded").toBe(true);
});

async function delayClientBundle(page: Page): Promise<void> {
  await page.route("**/_next/static/chunks/**", async (route) => {
    const { promise, resolve } = Promise.withResolvers<void>();
    setTimeout(resolve, BUNDLE_DELAY_MS);
    await promise;
    await route.continue();
  });
}

async function sendAndWait(page: Page, content: string): Promise<void> {
  await page.getByLabel("Message").fill(content);
  await page.getByRole("button", { name: "Send" }).click();
  const stop = page.getByRole("button", { name: "Stop" });
  // Stop exists only while a turn is in flight, so waiting for it to appear
  // proves the click actually started one — without it, "Stop has gone" would
  // be trivially true for a send that never happened.
  await expect(stop, "the turn started").toBeVisible();
  await expect(stop, "the turn finished").toBeHidden({ timeout: TURN_TIMEOUT_MS });
}

async function persistedMessages(
  request: APIRequestContext,
  conversationId: string,
): Promise<MessageOut[]> {
  const response = await request.get(`/api/conversations/${conversationId}`);
  expect(response.status(), "GET /conversations/{id}").toBe(200);
  const detail = (await response.json()) as ConversationDetail;
  return [...detail.messages].sort((a, b) => a.seq - b.seq);
}

function expectNothingCutOff(messages: MessageOut[]): void {
  // A dropped connection or a failed turn is recorded on the message itself
  // (contracts.md -> Streaming events -> Client disconnect).
  expect(
    messages.filter((m) => m.payload && "error" in m.payload).map(describeMessage),
    "persisted messages with payload.error",
  ).toEqual([]);
}

async function expectAnswersRenderedInFull(page: Page, messages: MessageOut[]): Promise<void> {
  const answers = messages.filter((m) => m.role === "assistant");
  expect(answers.length, "persisted assistant messages").toBeGreaterThan(0);
  const onScreen = normalise(await page.locator("main").innerText());
  for (const answer of answers) {
    expect(answer.content.trim(), `answer ${answer.seq} is non-empty`).not.toBe("");
    expect(
      onScreen.includes(normalise(answer.content)),
      `answer ${answer.seq} is on screen in full: ${describeMessage(answer)}`,
    ).toBe(true);
  }
}

// A document load replaces `window`, so a marker set on it is the sharpest
// available evidence that the chat page survived the whole conversation: a
// client-side route change keeps it, a reload or a native form submission
// does not.
const MARKER = "__manaLeakBrowserTest";

async function markDocument(page: Page): Promise<void> {
  await page.evaluate((marker) => {
    (window as unknown as Record<string, boolean>)[marker] = true;
  }, MARKER);
}

async function isSameDocument(page: Page): Promise<boolean> {
  return page.evaluate(
    (marker) => (window as unknown as Record<string, boolean>)[marker] === true,
    MARKER,
  );
}

function normalise(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function describeMessage(message: MessageOut): string {
  return `seq ${message.seq} ${message.role} payload=${JSON.stringify(message.payload)} content=${JSON.stringify(message.content)}`;
}
