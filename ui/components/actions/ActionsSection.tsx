"use client";

import { useMemo, useState } from "react";
import { Plus } from "lucide-react";
import {
  Action,
  actionsFromConfig,
  actionsToConfig,
  collectTriggerCodes,
  flattenForms,
  summarizeAction,
} from "@/lib/actions";
import ActionEditor from "./ActionEditor";
import ActionWizard from "./ActionWizard";

// Replaces the legacy `FormsSection` / `FormEditor`. Same persisted shape
// (`config.c_forms_selected`); friendlier UI on top.
//
// Layout: master-detail. Left: action list grouped by parent skill +
// orphan/shared groups. Right: editor for the selected action, or empty
// state.

type Props = {
  config: Record<string, unknown>;
  patch: (key: "c_forms_selected", value: unknown) => void;
  writable: boolean;
};

export default function ActionsSection({ config, patch, writable }: Props) {
  const actions = useMemo(() => actionsFromConfig(config.c_forms_selected), [config.c_forms_selected]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [wizardSkill, setWizardSkill] = useState<string | null>(null);

  const skillIds = Object.keys(
    (config.topics && typeof config.topics === "object" ? config.topics : {}) as Record<string, unknown>,
  );

  const safeIndex = actions.length === 0 ? 0 : Math.min(selectedIndex, actions.length - 1);
  const current = actions[safeIndex];

  const existingTriggerCodes = useMemo(() => {
    return collectTriggerCodes(flattenForms(config.c_forms_selected));
  }, [config.c_forms_selected]);

  function commitActions(next: Action[]) {
    patch("c_forms_selected", next.length > 0 ? actionsToConfig(next) : undefined);
  }

  function updateAt(index: number, fn: (a: Action) => Action) {
    commitActions(actions.map((a, i) => (i === index ? fn(a) : a)));
  }

  function rename(index: number, newName: string) {
    const trimmed = newName.trim();
    if (!trimmed) return;
    if (actions.some((a, i) => i !== index && a.name === trimmed)) return;
    updateAt(index, (a) => ({ ...a, name: trimmed }));
  }

  function remove(index: number) {
    if (!confirm(`Remove action "${actions[index]?.name}"?`)) return;
    commitActions(actions.filter((_, i) => i !== index));
    if (index <= selectedIndex && selectedIndex > 0) setSelectedIndex(selectedIndex - 1);
  }

  function startWizard(skill: string | null) {
    setWizardSkill(skill);
  }

  function finishWizard(action: Action) {
    let unique = action.name;
    let n = 1;
    while (actions.some((a) => a.name === unique)) {
      n += 1;
      unique = `${action.name}_${n}`;
    }
    const next = [...actions, { ...action, name: unique }];
    commitActions(next);
    setSelectedIndex(next.length - 1);
    setWizardSkill(null);
  }

  // Group actions by belongsTo[0]; surface orphans + multi-skill (shared).
  const groups = useMemo(() => groupActions(actions, skillIds), [actions, skillIds]);

  return (
    <div className="card" style={{ padding: 0 }}>
      <div className="row" style={{ alignItems: "stretch", gap: 0, minHeight: 480 }}>
        {/* Master pane */}
        <aside
          style={{
            width: 280,
            flexShrink: 0,
            borderRight: "1px solid var(--panel-border)",
            padding: 16,
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
        >
          <div>
            <h2 style={{ margin: 0, fontSize: 18 }}>Actions</h2>
            <p className="muted" style={{ fontSize: 12, margin: "4px 0 0" }}>
              Things the bot can do beyond replying with text — show forms, send alerts,
              open links.
            </p>
          </div>

          {writable && (
            <button onClick={() => startWizard(skillIds[0] ?? null)}>
              <Plus size={14} strokeWidth={1.75} /> New action
            </button>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: 16, overflowY: "auto" }}>
            {actions.length === 0 && (
              <div
                className="muted"
                style={{ fontSize: 13, padding: 12, textAlign: "center", border: "1px dashed var(--panel-border)", borderRadius: 6 }}
              >
                No actions yet. Click <strong>New action</strong> to walk through creating one.
              </div>
            )}

            {groups.map((group) => (
              <div key={group.key}>
                <div
                  className="eyebrow"
                  style={{
                    fontSize: 10,
                    color: "var(--text-muted)",
                    textTransform: "uppercase",
                    letterSpacing: 0.5,
                    marginBottom: 6,
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <span>{group.label}</span>
                  {writable && group.kind === "skill" && (
                    <button
                      className="ghost compact-icon"
                      onClick={() => startWizard(group.skillId ?? null)}
                      title={`Add action attached to ${group.label}`}
                    >
                      +
                    </button>
                  )}
                </div>
                <div style={{ display: "grid", gap: 4 }}>
                  {group.items.map(({ action, index }) => {
                    const active = index === safeIndex;
                    return (
                      <button
                        key={action.name}
                        className="ghost"
                        onClick={() => setSelectedIndex(index)}
                        style={{
                          textAlign: "left",
                          padding: "8px 10px",
                          borderLeft: active ? "2px solid var(--accent)" : "2px solid transparent",
                          background: active ? "var(--panel-bg)" : "transparent",
                          display: "block",
                        }}
                      >
                        <div style={{ fontSize: 13, fontWeight: 500 }}>{action.name}</div>
                        <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
                          {summarizeAction(action)}
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </aside>

        {/* Detail pane */}
        <main style={{ flex: 1, padding: 20, minWidth: 0 }}>
          {!current ? (
            <EmptyState skillIds={skillIds} writable={writable} onCreate={() => startWizard(skillIds[0] ?? null)} />
          ) : (
            <ActionEditor
              key={current.name + safeIndex}
              action={current}
              skillIds={skillIds}
              existingTriggerCodes={existingTriggerCodes}
              writable={writable}
              onChange={(next) => updateAt(safeIndex, () => next)}
              onRename={(n) => rename(safeIndex, n)}
              onRemove={() => remove(safeIndex)}
            />
          )}
        </main>
      </div>

      {wizardSkill !== null && (
        <ActionWizard
          skillName={wizardSkill ?? ""}
          skillIds={skillIds}
          existingTriggerCodes={existingTriggerCodes}
          existingActionNames={actions.map((a) => a.name)}
          onCancel={() => setWizardSkill(null)}
          onSave={finishWizard}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Grouping
// ---------------------------------------------------------------------------

type Group = {
  key: string;
  label: string;
  kind: "skill" | "shared" | "orphan";
  /** Only set when kind === "skill". */
  skillId?: string;
  items: { action: Action; index: number }[];
};

function groupActions(actions: Action[], skillIds: string[]): Group[] {
  const knownSkill = new Set(skillIds);
  const bySkill = new Map<string, { action: Action; index: number }[]>();
  const shared: { action: Action; index: number }[] = [];
  const orphan: { action: Action; index: number }[] = [];

  for (let i = 0; i < actions.length; i++) {
    const action = actions[i];
    if (action.belongsTo.length === 0) {
      orphan.push({ action, index: i });
      continue;
    }
    if (action.belongsTo.length > 1) {
      shared.push({ action, index: i });
      continue;
    }
    const skill = action.belongsTo[0];
    const arr = bySkill.get(skill) ?? [];
    arr.push({ action, index: i });
    bySkill.set(skill, arr);
  }

  const groups: Group[] = [];
  // Preserve skill order from the config; append unknown skills at the end.
  const orderedSkills: string[] = [];
  for (const id of skillIds) if (bySkill.has(id)) orderedSkills.push(id);
  for (const id of Array.from(bySkill.keys())) {
    if (!knownSkill.has(id)) orderedSkills.push(id);
  }
  for (const id of orderedSkills) {
    const items = bySkill.get(id) ?? [];
    if (items.length === 0) continue;
    groups.push({
      key: `skill-${id}`,
      label: knownSkill.has(id) ? id : `${id} (missing)`,
      kind: "skill",
      skillId: id,
      items,
    });
  }
  if (shared.length > 0) {
    groups.push({ key: "shared", label: "Shared (multi-skill)", kind: "shared", items: shared });
  }
  if (orphan.length > 0) {
    groups.push({ key: "orphan", label: "Unattached", kind: "orphan", items: orphan });
  }
  return groups;
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState({
  skillIds,
  writable,
  onCreate,
}: {
  skillIds: string[];
  writable: boolean;
  onCreate: () => void;
}) {
  return (
    <div style={{ padding: 24 }}>
      <h3 style={{ marginTop: 0 }}>Actions</h3>
      <p className="muted" style={{ fontSize: 13 }}>
        Actions let the bot do something during the conversation beyond replying with text — for
        example, show a callback form when a visitor agrees to a phone call, or email an alert
        when someone asks for pricing.
      </p>
      <h4>Common patterns</h4>
      <ul style={{ paddingLeft: 20, fontSize: 13 }}>
        <li>
          <strong>Lead capture:</strong> attach a "Show form" action to a sales skill and have it
          fire when the bot decides the visitor is interested.
        </li>
        <li>
          <strong>Hand-off alert:</strong> email your support inbox when the bot can&apos;t answer
          a question from the knowledge base.
        </li>
        <li>
          <strong>Tracked CTA:</strong> send the visitor to a UTM-tagged landing page when they ask
          about pricing.
        </li>
      </ul>
      {writable && (
        <button onClick={onCreate} style={{ marginTop: 12 }}>
          <Plus size={14} strokeWidth={1.75} /> New action
        </button>
      )}
      {!writable && (
        <p className="muted" style={{ fontSize: 12, marginTop: 12 }}>
          Your role doesn&apos;t allow editing actions on this bot.
        </p>
      )}
      {skillIds.length === 0 && (
        <p className="muted" style={{ fontSize: 12, marginTop: 12 }}>
          Tip: create at least one skill on the <strong>Topics</strong> tab first — actions attach
          to skills.
        </p>
      )}
    </div>
  );
}
