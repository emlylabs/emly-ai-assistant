import { initializeChatbot, destroyChatbot } from './embed';

// Define the ChatbotWidget API
const ChatbotWidget = {
    initialize: initializeChatbot,
    destroy: destroyChatbot,
};

// Expose to window globally
if (typeof window !== 'undefined') {
    (window as any).ChatbotWidget = ChatbotWidget;
}

// Also export as module default
export default ChatbotWidget;
export { initializeChatbot, destroyChatbot };
