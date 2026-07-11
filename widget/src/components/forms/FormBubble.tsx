import React, { useState } from 'react';
import { Bot } from 'lucide-react';
import JsonToForm, { type FormSchema } from './JsonToForm';
import type { MessageForm } from '../../utils/sessionManager';

interface FormBubbleProps {
  /** Inline form payload pulled from the message. */
  form: MessageForm;
  chatIcon?: string;
  /** Async submit — POSTs the form values to `/widget/{bot}/action`.
   * The bubble shows a busy state while this resolves. */
  onSubmit: (values: Record<string, unknown>) => Promise<void>;
  onCancel: () => void;
  /** When the trigger has `user_confirmation`, the bubble starts as a
   * Yes/No prompt; the visitor accepts before the fields appear. */
  onConfirmationChoice?: (choice: 'accepted' | 'declined') => void;
  /** Click handler for the post-limit engagement chip. The chip's
   * label is `trigger.post_limit_query.attention_query`; clicking it
   * sends that string as a regular user message. */
  onPostLimitEngage?: (query: string) => void;
  /** Verify the OTP entered by the visitor. Resolves to `null` on
   * success or an error message string on failure. */
  onVerifyOtp?: (otp: string) => Promise<string | null>;
  /** Re-send the OTP. Resolves to `null` on success or an error
   * message string on failure. */
  onResendOtp?: () => Promise<string | null>;
}

interface OtpStepProps {
  email?: string;
  onVerify: (otp: string) => Promise<string | null>;
  onResend: () => Promise<string | null>;
}

/** Inline OTP entry shown when the form's trigger has
 * `verify_action: true`. Mirrors the `OTP` UI in
 * `your-assistant/src/UI-Components/Forms/GeneralForm.js`. */
const OtpStep: React.FC<OtpStepProps> = ({ email, onVerify, onResend }) => {
  const [otp, setOtp] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resendStatus, setResendStatus] = useState<string | null>(null);

  const handleVerify = async () => {
    if (!otp.trim()) {
      setError('Enter the code we emailed you');
      return;
    }
    setBusy(true);
    setError(null);
    setResendStatus(null);
    const err = await onVerify(otp.trim());
    if (err) setError(err);
    setBusy(false);
  };

  const handleResend = async () => {
    setBusy(true);
    setResendStatus(null);
    setError(null);
    const err = await onResend();
    setResendStatus(err ?? 'Code resent');
    setBusy(false);
  };

  return (
    <div className="emw-form">
      <div className="emw-form-desc">
        We sent a verification code{email ? ` to ${email}` : ''}. Enter it below to continue.
      </div>
      <div className="emw-form-row">
        <label htmlFor="emw-otp-input">Verification code</label>
        <input
          id="emw-otp-input"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          value={otp}
          disabled={busy}
          onChange={e => setOtp(e.target.value)}
          aria-invalid={error ? true : undefined}
        />
        {error && <div className="emw-form-error">{error}</div>}
        {resendStatus && !error && <div className="emw-form-footnote">{resendStatus}</div>}
      </div>
      <div className="emw-form-actions">
        <button
          type="button"
          className="emw-form-btn emw-form-btn--ghost"
          onClick={handleResend}
          disabled={busy}
        >
          Resend
        </button>
        <button
          type="button"
          className="emw-form-btn"
          onClick={handleVerify}
          disabled={busy}
        >
          {busy ? 'Verifying…' : 'Verify'}
        </button>
      </div>
    </div>
  );
};

/** Renders a chat-message bubble that contains either a confirmation
 * prompt or an inline form. After submission the bubble flips to the
 * configured `post_submission_message` so the visitor has a stable
 * record of what they sent. Equivalent to the `renderForm` /
 * `formPromptComponent` paths in `your-assistant/src/UI-Components/Chat.js`. */
const FormBubble: React.FC<FormBubbleProps> = ({
  form,
  chatIcon,
  onSubmit,
  onCancel,
  onConfirmationChoice,
  onPostLimitEngage,
  onVerifyOtp,
  onResendOtp,
}) => {
  const [busy, setBusy] = useState(false);
  const schema = (form.formSchema as FormSchema) ?? {};
  const trigger = form.trigger ?? {};

  const renderAvatar = () =>
    chatIcon ? (
      <img src={chatIcon} alt="Bot Avatar" className="emw-avatar" />
    ) : (
      <span className="emw-avatar emw-avatar--icon" aria-hidden="true">
        <Bot size={18} />
      </span>
    );

  // Submitted / cancelled — collapse the bubble to a status note.
  if (form.status === 'submitted') {
    const msg = (schema.post_submission_message as string) || "Thanks — we've received your details.";
    return (
      <div className="emw-message emw-bot">
        {renderAvatar()}
        <div className="emw-content emw-bot-content">
          <div className="emw-message-bubble emw-bot-bubble emw-form-bubble">{msg}</div>
        </div>
      </div>
    );
  }
  if (form.status === 'cancelled') {
    return null;
  }

  // Post-limit engagement — visitor has hit `trigger.limit` for this
  // form. Render the configured `attention_text` plus an optional chip
  // that sends `attention_query` as a regular user message.
  if (form.status === 'post-limit') {
    const postLimit = (trigger.post_limit_query as { attention_text?: string; attention_query?: string }) || {};
    const text = postLimit.attention_text || "You've reached the submission limit for this form.";
    const query = postLimit.attention_query;
    return (
      <div className="emw-message emw-bot">
        {renderAvatar()}
        <div className="emw-content emw-bot-content">
          <div className="emw-message-bubble emw-bot-bubble emw-form-bubble">
            <div className="emw-form-confirm-msg">{text}</div>
            {query && onPostLimitEngage && (
              <ul className="emw-action-chips" role="list">
                <li>
                  <button type="button" onClick={() => onPostLimitEngage(query)}>{query}</button>
                </li>
              </ul>
            )}
          </div>
        </div>
      </div>
    );
  }

  // Confirmation gate — the trigger sets `user_confirmation`, we ask
  // Yes/No first and only swap to the form when the visitor accepts.
  const userConfirmation = Boolean(trigger.user_confirmation);
  if (userConfirmation && form.confirm !== 'accepted') {
    if (form.confirm === 'declined') return null;
    const message = (trigger.user_confirmation_message as string) || 'Want to fill in a quick form?';
    return (
      <div className="emw-message emw-bot">
        {renderAvatar()}
        <div className="emw-content emw-bot-content">
          <div className="emw-message-bubble emw-bot-bubble emw-form-bubble">
            <div className="emw-form-confirm-msg">{message}</div>
            <div className="emw-form-actions">
              <button
                type="button"
                className="emw-form-btn emw-form-btn--ghost"
                onClick={() => onConfirmationChoice?.('declined')}
              >
                No
              </button>
              <button
                type="button"
                className="emw-form-btn"
                onClick={() => onConfirmationChoice?.('accepted')}
              >
                Yes
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // OTP verification — submit attempt set `otpStep=awaitingOtp` and
  // we're now showing the code-entry sub-component instead of the
  // form fields. Verify replays the original values; Resend re-runs
  // the send.
  if (form.otpStep === 'awaitingOtp' && onVerifyOtp && onResendOtp) {
    return (
      <div className="emw-message emw-bot">
        {renderAvatar()}
        <div className="emw-content emw-bot-content">
          <div className="emw-message-bubble emw-bot-bubble emw-form-bubble">
            <OtpStep
              email={form.otpEmail}
              onVerify={onVerifyOtp}
              onResend={onResendOtp}
            />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="emw-message emw-bot">
      {renderAvatar()}
      <div className="emw-content emw-bot-content">
        <div className="emw-message-bubble emw-bot-bubble emw-form-bubble">
          <JsonToForm
            schema={schema}
            busy={busy}
            formCount={form.formCount}
            onCancel={onCancel}
            onSubmit={async (values) => {
              setBusy(true);
              try {
                await onSubmit(values);
              } finally {
                setBusy(false);
              }
            }}
          />
        </div>
      </div>
    </div>
  );
};

export default FormBubble;
