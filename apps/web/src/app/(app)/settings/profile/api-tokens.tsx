"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ConfirmDialog,
  DialogRoot,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Notice,
  Select,
  Skeleton,
  toast,
} from "@klick/ui";
import { Copy, KeyRound, Plus } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { api, qk, unwrap, type Schemas } from "@/lib/api";
import { useActiveOrg } from "@/lib/queries";
import { fieldErrors, useErrorMessage } from "@/lib/use-error-message";

type Token = Schemas["TokenOut"];

export function ApiTokens() {
  const t = useTranslations("profile.tokens");
  const errorMessage = useErrorMessage();
  const format = useFormatter();
  const qc = useQueryClient();
  const { active } = useActiveOrg();
  const orgId = active?.id ?? null;
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [days, setDays] = useState("90");
  const [scopes, setScopes] = useState<string[]>([]);
  const [created, setCreated] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<Token | null>(null);

  const tokens = useQuery({
    queryKey: qk.tokens(orgId),
    queryFn: () => unwrap(api.GET("/api/v1/me/api-tokens")),
    enabled: !!orgId,
  });

  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/me/api-tokens", {
          body: { name: name.trim(), scopes, expires_in_days: Number(days) },
        }),
      ),
    onSuccess: (res) => {
      setCreating(false);
      setCreated(res.token);
      setName("");
      setScopes([]);
      return qc.invalidateQueries({ queryKey: qk.tokens(orgId) });
    },
  });

  const revoke = useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.DELETE("/api/v1/me/api-tokens/{token_id}", { params: { path: { token_id: id } } }),
      ),
    onSuccess: () => {
      toast.success(t("revoked"));
      setRevoking(null);
      return qc.invalidateQueries({ queryKey: qk.tokens(orgId) });
    },
    onError: (e) => {
      setRevoking(null);
      toast.error(errorMessage(e));
    },
  });

  if (!active) return null;
  const available = [...active.permissions].sort();
  const server = fieldErrors(create.error);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  const toggle = (scope: string) =>
    setScopes((s) => (s.includes(scope) ? s.filter((x) => x !== scope) : [...s, scope]));

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(created ?? "");
      toast.success(t("copied"));
    } catch {
      toast.error(t("copyFailed"));
    }
  };

  return (
    <section
      aria-labelledby="tok-h"
      className="flex flex-col gap-3 rounded-card border border-border-1 bg-bg-1 p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="tok-h" className="text-lg">
            {t("title")}
          </h2>
          <p className="m-0 text-text-2">{t("body", { org: active.name })}</p>
        </div>
        <Button icon={<Plus size={16} aria-hidden />} onClick={() => setCreating(true)}>
          {t("new")}
        </Button>
      </div>

      {tokens.isPending ? (
        <Skeleton className="h-14 w-full" />
      ) : tokens.isError ? (
        <ErrorState
          title={t("loadFailed")}
          description={errorMessage(tokens.error)}
          retryLabel={t("retry")}
          onRetry={() => void tokens.refetch()}
        />
      ) : tokens.data.length === 0 ? (
        <EmptyState icon={KeyRound} title={t("emptyTitle")} description={t("emptyBody")} />
      ) : (
        <ul className="m-0 flex list-none flex-col divide-y divide-border-1 p-0">
          {tokens.data.map((tk) => (
            <li key={tk.id} className="flex flex-wrap items-center gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="m-0 font-medium">{tk.name}</p>
                <p className="m-0 text-sm text-text-2">
                  <span className="font-mono">{tk.prefix}…</span> · {tk.scopes.length} {t("scopes")}{" "}
                  ·{" "}
                  {tk.last_used_at
                    ? t("lastUsed", {
                        date: format.dateTime(new Date(tk.last_used_at), { dateStyle: "medium" }),
                      })
                    : t("neverUsed")}
                  {tk.expires_at
                    ? ` · ${t("expires", {
                        date: format.dateTime(new Date(tk.expires_at), { dateStyle: "medium" }),
                      })}`
                    : ""}
                </p>
              </div>
              <Button variant="ghost" size="dense" onClick={() => setRevoking(tk)}>
                {t("revoke")}
              </Button>
            </li>
          ))}
        </ul>
      )}

      <DialogRoot
        open={creating}
        onOpenChange={setCreating}
        title={t("newTitle")}
        description={t("newBody")}
        closeLabel={t("close")}
      >
        <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
          <Field label={t("name")} error={server.name}>
            {(a) => (
              <Input {...a} maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
            )}
          </Field>
          <Field label={t("expiry")}>
            {(a) => (
              <Select {...a} value={days} onChange={(e) => setDays(e.target.value)}>
                {["30", "90", "180", "365"].map((d) => (
                  <option key={d} value={d}>
                    {t("days", { n: Number(d) })}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <fieldset className="m-0 flex max-h-48 flex-col gap-1 overflow-y-auto rounded-card border border-border-2 p-3">
            <legend className="px-1 text-sm font-medium">{t("scopesLegend")}</legend>
            {available.map((s) => (
              <label key={s} className="flex min-h-8 items-center gap-2 font-mono text-sm">
                <input
                  type="checkbox"
                  className="size-4 accent-[var(--accent-text)]"
                  checked={scopes.includes(s)}
                  onChange={() => toggle(s)}
                />
                {s}
              </label>
            ))}
          </fieldset>
          {create.isError && !server.name ? (
            <Notice tone="danger">{errorMessage(create.error)}</Notice>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button onClick={() => setCreating(false)}>{t("cancel")}</Button>
            <Button
              type="submit"
              variant="primary"
              loading={create.isPending}
              disabled={!name.trim() || scopes.length === 0}
            >
              {t("create")}
            </Button>
          </div>
        </form>
      </DialogRoot>

      <DialogRoot
        open={created !== null}
        onOpenChange={(o) => !o && setCreated(null)}
        title={t("createdTitle")}
        description={t("createdBody")}
        closeLabel={t("close")}
      >
        <div className="flex flex-col gap-4">
          <Input
            readOnly
            aria-label={t("tokenValue")}
            className="font-mono"
            value={created ?? ""}
            onFocus={(e) => e.currentTarget.select()}
          />
          <div className="flex justify-end gap-2">
            <Button icon={<Copy size={16} aria-hidden />} onClick={() => void copy()}>
              {t("copy")}
            </Button>
            <Button variant="primary" onClick={() => setCreated(null)}>
              {t("done")}
            </Button>
          </div>
        </div>
      </DialogRoot>

      <ConfirmDialog
        open={!!revoking}
        onOpenChange={(o) => !o && setRevoking(null)}
        title={t("revokeTitle", { name: revoking?.name ?? "" })}
        description={t("revokeBody")}
        confirmLabel={t("revokeConfirm")}
        cancelLabel={t("cancel")}
        closeLabel={t("close")}
        loading={revoke.isPending}
        onConfirm={() => revoking && revoke.mutate(revoking.id)}
      />
    </section>
  );
}
