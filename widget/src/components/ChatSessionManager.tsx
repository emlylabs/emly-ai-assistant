import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Bot, Check, ChevronsLeft, Trash2, X } from 'lucide-react';

import { type ChatSession } from '../utils/sessionManager';

interface ChatSessionManagerProps {
    sessions: ChatSession[];
    activeSessionId: string | null;
    onSwitchSession: (sessionId: string) => void;
    onDeleteSession: (sessionId: string) => void;
    onToggleSessionDropdown: () => void;
    showHistory: boolean;
    headerIcon?: string;
}

const CONFIRM_TIMEOUT_MS = 4000;

const ChatSessionManager: React.FC<ChatSessionManagerProps> = ({
    sessions,
    activeSessionId,
    onSwitchSession,
    onDeleteSession,
    showHistory,
    headerIcon,
    onToggleSessionDropdown,
}) => {
    const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const clearConfirm = useCallback(() => {
        setConfirmDeleteId(null);
        if (timerRef.current) {
            clearTimeout(timerRef.current);
            timerRef.current = null;
        }
    }, []);

    const requestDelete = (event: React.MouseEvent, sessionId: string) => {
        event.stopPropagation();
        if (sessions.length <= 1) return;
        setConfirmDeleteId(sessionId);
        if (timerRef.current) clearTimeout(timerRef.current);
        timerRef.current = setTimeout(() => {
            setConfirmDeleteId((curr) => (curr === sessionId ? null : curr));
            timerRef.current = null;
        }, CONFIRM_TIMEOUT_MS);
    };

    const confirmDelete = (event: React.MouseEvent, sessionId: string) => {
        event.stopPropagation();
        if (sessions.length <= 1) return;
        clearConfirm();
        onDeleteSession(sessionId);
    };

    const cancelDelete = (event: React.MouseEvent) => {
        event.stopPropagation();
        clearConfirm();
    };

    const handleSessionClick = (sessionId: string) => {
        if (sessions.length <= 1) return;
        if (confirmDeleteId) {
            clearConfirm();
            return;
        }
        onSwitchSession(sessionId);
    };

    // Dismiss pending confirmation on Esc — matches the kebab-menu behavior the
    // old context menu had, so keyboard users can back out of a delete prompt.
    useEffect(() => {
        if (!confirmDeleteId) return;
        const handleEsc = (e: KeyboardEvent) => {
            if (e.key === 'Escape') clearConfirm();
        };
        window.addEventListener('keydown', handleEsc);
        return () => window.removeEventListener('keydown', handleEsc);
    }, [confirmDeleteId, clearConfirm]);

    useEffect(() => () => {
        if (timerRef.current) clearTimeout(timerRef.current);
    }, []);

    if (!showHistory) {
        return null;
    }

    return (
        <div className='em-left-window'>
            <div className="session-management-container">
                <div className='session-management-header'>
                    {headerIcon ? (
                        <img className='session-management-header-icon' src={headerIcon} alt="" />
                    ) : (
                        <span className='session-management-header-icon session-management-header-icon--lucide' aria-hidden="true">
                            <Bot size={18} />
                        </span>
                    )}
                    <span className='session-management-header-text'>History</span>
                    <button
                        type="button"
                        className="session-management-close-icon"
                        aria-label="Close history"
                        title="Collapse"
                        onClick={onToggleSessionDropdown}
                    >
                        <ChevronsLeft size={18} aria-hidden="true" />
                    </button>
                </div>
                <div className="session-dropdown">
                    {sessions.map(session => {
                        const isConfirming = confirmDeleteId === session.id;
                        const canDelete = sessions.length > 1;
                        return (
                            <div
                                key={session.id}
                                className={`session-item ${session.id === activeSessionId ? 'active' : ''} ${isConfirming ? 'is-confirming' : ''}`}
                                onClick={() => handleSessionClick(session.id)}
                                style={!canDelete ? { cursor: 'default' } : undefined}
                            >
                                <span className="session-item-name">{session.name}</span>
                                {canDelete && (
                                    isConfirming ? (
                                        <span className="session-item-confirm" role="group" aria-label="Confirm delete">
                                            <button
                                                type="button"
                                                className="session-item-confirm__btn session-item-confirm__yes"
                                                onClick={(e) => confirmDelete(e, session.id)}
                                                aria-label="Confirm delete"
                                                title="Delete"
                                            >
                                                <Check size={13} aria-hidden="true" />
                                            </button>
                                            <button
                                                type="button"
                                                className="session-item-confirm__btn session-item-confirm__no"
                                                onClick={cancelDelete}
                                                aria-label="Cancel"
                                                title="Cancel"
                                            >
                                                <X size={13} aria-hidden="true" />
                                            </button>
                                        </span>
                                    ) : (
                                        <button
                                            type="button"
                                            className="session-delete-trigger"
                                            onClick={(e) => requestDelete(e, session.id)}
                                            aria-label="Delete session"
                                            title="Delete"
                                        >
                                            <Trash2 size={14} aria-hidden="true" />
                                        </button>
                                    )
                                )}
                            </div>
                        );
                    })}
                </div>
            </div>
        </div>
    );
};

export default ChatSessionManager;
