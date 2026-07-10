// Holds the active bot id for the current widget instance. Lives in its
// own module so `sessionManager.ts` can read it without importing the
// `embed.tsx` entry (which has window side-effects and would create an
// import cycle through the redux store).
//
// Set by `initializeChatbot` before the React tree renders, so any code
// that runs after mount sees a stable id.

let _botId = '';

export const setBotId = (id: string | undefined): void => {
    _botId = id ?? '';
};

export const getBotId = (): string => _botId;
