import React from 'react';
import { Bot, History, Maximize2, Minimize2, Plus, X } from 'lucide-react';

interface ChatbotHeaderProps {
    headerIcon?: string;
    /** Optional URL override for the close button (config `close_icon`). */
    closeIconUrl?: string;
    onClose: () => void;
    onToggleMaximize: () => void;
    isMaximized: boolean;
    title?: string;
    subtitle?: string;
    headline?: string;
    onNewSession: () => void;
    showHistory: boolean;
    /** Toggle visibility of the maximize/restore button (config `show_min_max`). */
    showMinMax?: boolean;
    /** Toggle visibility of the close button (config `show_close`). */
    showClose?: boolean;
    onToggleSessionDropdown: () => void;
    // onToggleThemeMenu?: () => void;
    // activeThemeName?: string;
}

const ChatbotHeader: React.FC<ChatbotHeaderProps> = ({
    headerIcon,
    closeIconUrl,
    onClose,
    onToggleMaximize,
    isMaximized,
    title,
    subtitle,
    headline,
    onNewSession,
    showHistory,
    showMinMax = true,
    showClose = true,
    onToggleSessionDropdown,
    // onToggleThemeMenu,
    // activeThemeName,
}) => {

    return (
        <>
            <div className="header">
                {showHistory && (
                    <button
                        type="button"
                        className="session-icon"
                        aria-label="Sessions"
                        onClick={onToggleSessionDropdown}
                    >
                        <History size={18} aria-hidden="true" />
                    </button>
                )}
                {headerIcon ? (
                    <img src={headerIcon} alt="User Avatar" className="avatar" />
                ) : (
                    <span className="avatar avatar--icon" aria-hidden="true">
                        <Bot size={22} />
                    </span>
                )}
                <div className="header-text">
                    {title}
                    {subtitle && <p>{subtitle}</p>}
                </div>
                <div className="header-icons">
                    <button
                        type="button"
                        className="new-chat-container"
                        aria-label="New Chat"
                        onClick={onNewSession}
                    >
                        <Plus size={18} className="new-chat-icon" aria-hidden="true" />
                    </button>
                    {/* Theme selector button - hidden
                    {onToggleThemeMenu && (
                        <button
                            type="button"
                            className="emw-theme-button"
                            onClick={onToggleThemeMenu}
                            aria-label="Change theme"
                            title={activeThemeName ? `Theme: ${activeThemeName}` : 'Change theme'}
                        >
                            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                                <path d="M12 3a9 9 0 1 0 9 9c0-.43-.03-.86-.09-1.28a1 1 0 0 0-1.38-.76 3 3 0 0 1-3.78-3.78 1 1 0 0 0-.76-1.38C12.86 3.03 12.43 3 12 3Zm0 2c.2 0 .4.01.6.03a5 5 0 0 0 6.37 6.37c.02.2.03.4.03.6a7 7 0 1 1-7-7Z" />
                            </svg>
                        </button>
                    )}
                    */}
                    {showMinMax && (
                        <button
                            type="button"
                            className="maximize-icon"
                            onClick={onToggleMaximize}
                            aria-label={isMaximized ? 'Restore chat size' : 'Maximize chat size'}
                            title={isMaximized ? 'Restore' : 'Maximize'}
                        >
                            {isMaximized ? (
                                <Minimize2 size={18} aria-hidden="true" />
                            ) : (
                                <Maximize2 size={18} aria-hidden="true" />
                            )}
                        </button>
                    )}
                    {showClose && (
                        <button
                            type="button"
                            className="close-icon"
                            aria-label="Close"
                            onClick={onClose}
                        >
                            {closeIconUrl ? (
                                <img src={closeIconUrl} alt="" className="close-icon-img" />
                            ) : (
                                <X size={18} aria-hidden="true" />
                            )}
                        </button>
                    )}
                </div>
            </div>
            <div className="banner">
                {headline}
            </div>
        </>
    );
};

export default ChatbotHeader;
