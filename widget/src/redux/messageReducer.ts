import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import {
    type Message,
    type ChatSession,
    getSessions,
    saveSessions,
    getActiveSessionId,
    setActiveSessionId,
    createNewSession,
    deleteSession as deleteSessionFromManager,
    clearActiveSessionId,
} from '../utils/sessionManager';

interface MessageState {
    sessions: ChatSession[];
    activeSessionId: string | null;
    messages: Message[]; // Messages of the active session
}

// The slice is created at module load — before `initializeChatbot` sets
// the bot id — so we can't read per-bot storage here without leaking a
// fresh "Session 1" into an unscoped key. Start empty; the embed entry
// dispatches `rehydrateForBot` once botId is known to populate state
// from the per-bot bucket (creating a default session if needed).
const initialState: MessageState = {
    sessions: [],
    activeSessionId: null,
    messages: [],
};

const messageSlice = createSlice({
    name: 'messages',
    initialState,
    reducers: {
        rehydrateForBot: (state) => {
            let sessions = getSessions();
            let activeSessionId = getActiveSessionId();

            if (sessions.length === 0 || !activeSessionId || !sessions.some(s => s.id === activeSessionId)) {
                const newSession = createNewSession("Session 1");
                sessions = [newSession];
                activeSessionId = newSession.id;
            }

            const activeSession = sessions.find(session => session.id === activeSessionId);
            state.sessions = sessions;
            state.activeSessionId = activeSessionId;
            state.messages = activeSession ? activeSession.messages : [];
        },
        addMessage: (state, action: PayloadAction<Message>) => {
            state.messages.push(action.payload);
            if (state.activeSessionId) {
                const sessionIndex = state.sessions.findIndex(session => session.id === state.activeSessionId);
                if (sessionIndex !== -1) {
                    state.sessions[sessionIndex].messages = state.messages;
                    saveSessions(state.sessions); // Save updated sessions to local storage
                }
            }
        },
        // Patch an existing message's `form` block — used to flip a
        // form bubble between confirm/pending/submitted/cancelled
        // without dropping the rest of the message (text, timestamp).
        updateMessageForm: (
            state,
            action: PayloadAction<{ id: string; form: Partial<Message['form']> & object }>,
        ) => {
            const { id, form } = action.payload;
            const target = state.messages.find(m => m.id === id);
            if (!target || !target.form) return;
            target.form = { ...target.form, ...form };
            if (state.activeSessionId) {
                const idx = state.sessions.findIndex(s => s.id === state.activeSessionId);
                if (idx !== -1) {
                    state.sessions[idx].messages = state.messages;
                    saveSessions(state.sessions);
                }
            }
        },
        deleteMessageById: (state, action: PayloadAction<string>) => {
            state.messages = state.messages.filter(message => message.id !== action.payload);
            if (state.activeSessionId) {
                const sessionIndex = state.sessions.findIndex(session => session.id === state.activeSessionId);
                if (sessionIndex !== -1) {
                    state.sessions[sessionIndex].messages = state.messages;
                    saveSessions(state.sessions);
                }
            }
        },
        deleteAllMessages: (state) => {
            state.messages = [];
            if (state.activeSessionId) {
                const sessionIndex = state.sessions.findIndex(session => session.id === state.activeSessionId);
                if (sessionIndex !== -1) {
                    state.sessions[sessionIndex].messages = state.messages;
                    saveSessions(state.sessions);
                }
            }
        },
        deleteMessagesFromTop: (state, action: PayloadAction<number>) => {
            state.messages = state.messages.slice(action.payload);
            if (state.activeSessionId) {
                const sessionIndex = state.sessions.findIndex(session => session.id === state.activeSessionId);
                if (sessionIndex !== -1) {
                    state.sessions[sessionIndex].messages = state.messages;
                    saveSessions(state.sessions);
                }
            }
        },
        addSession: (state, action: PayloadAction<ChatSession>) => {
            state.sessions.push(action.payload);
            saveSessions(state.sessions);
        },
        switchSession: (state, action: PayloadAction<string>) => {
            const newActiveSessionId = action.payload;
            const latestSessions = getSessions(); // Re-fetch sessions from local storage
            const newActiveSession = latestSessions.find(session => session.id === newActiveSessionId);
            if (newActiveSession) {
                state.activeSessionId = newActiveSessionId;
                state.messages = newActiveSession.messages;
                setActiveSessionId(newActiveSessionId);
                state.sessions = latestSessions; // Update Redux state's sessions array
            }
        },
        deleteSession: (state, action: PayloadAction<string>) => {
            const sessionIdToDelete = action.payload;
            deleteSessionFromManager(sessionIdToDelete); // Delete from local storage
            state.sessions = state.sessions.filter(session => session.id !== sessionIdToDelete); // Update Redux state sessions

            if (state.activeSessionId === sessionIdToDelete) {
                if (state.sessions.length > 0) {
                    state.activeSessionId = state.sessions[0].id;
                    setActiveSessionId(state.sessions[0].id);
                    state.messages = state.sessions[0].messages; // Update messages after session switch
                } else {
                    state.activeSessionId = null;
                    state.messages = []; // Clear messages if no sessions left
                    clearActiveSessionId(); // Clear active session id from local storage
                }
            }
            saveSessions(state.sessions);
        },
        updateSessionName: (state, action: PayloadAction<{ sessionId: string; newName: string }>) => {
            const { sessionId, newName } = action.payload;
            const sessionToUpdate = state.sessions.find(session => session.id === sessionId);
            if (sessionToUpdate) {
                sessionToUpdate.name = newName;
                saveSessions(state.sessions);
            }
        },
    },
});

export const {
    rehydrateForBot,
    addMessage,
    updateMessageForm,
    deleteMessageById,
    deleteAllMessages,
    deleteMessagesFromTop,
    addSession,
    switchSession,
    deleteSession,
    updateSessionName,
} = messageSlice.actions;

export default messageSlice.reducer;
