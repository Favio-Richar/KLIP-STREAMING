"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Notice,
  Select,
  Skeleton,
  toast,
} from "@klick/ui";
import { MailPlus, Trash2, Users } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { api, qk, unwrap, type Schemas } from "@/lib/api";
import { can, useActiveOrg } from "@/lib/queries";
import { fieldErrors, useErrorMessage } from "@/lib/use-error-message";

type Member = Schemas["MemberOut"];
const ROLES = ["owner", "admin", "editor", "reviewer", "viewer"] as const;
type Role = (typeof ROLES)[number];

export function MembersScreen() {
  const t = useTranslations("members");
  const { active, isPending } = useActiveOrg();

  if (isPending) return <Skeleton className="h-64 w-full" />;
  if (!active) return <EmptyState icon={Users} title={t("noOrg")} />;
  const manage = can(active, "members:manage");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl">{t("title")}</h1>
        <p className="m-0 text-text-2">{t("subtitle", { org: active.name })}</p>
      </div>
      {!manage ? <Notice tone="info">{t("readOnly")}</Notice> : null}
      <MemberList orgId={active.id} manage={manage} />
      {manage ? <Invitations orgId={active.id} /> : null}
    </div>
  );
}

function MemberList({ orgId, manage }: { orgId: string; manage: boolean }) {
  const t = useTranslations("members");
  const tr = useTranslations("panel.roles");
  const errorMessage = useErrorMessage();
  const qc = useQueryClient();
  const [removing, setRemoving] = useState<Member | null>(null);

  const members = useQuery({
    queryKey: qk.members(orgId),
    queryFn: () => unwrap(api.GET("/api/v1/members")),
  });

  const changeRole = useMutation({
    mutationFn: (v: { id: string; role: Role }) =>
      unwrap(
        api.PATCH("/api/v1/members/{member_id}", {
          params: { path: { member_id: v.id } },
          body: { role: v.role },
        }),
      ),
    onSuccess: () => {
      toast.success(t("roleChanged"));
      return qc.invalidateQueries({ queryKey: qk.members(orgId) });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const remove = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/members/{member_id}", { params: { path: { member_id: id } } })),
    onSuccess: () => {
      toast.success(t("removed"));
      setRemoving(null);
      return qc.invalidateQueries({ queryKey: qk.members(orgId) });
    },
    onError: (e) => {
      setRemoving(null);
      toast.error(errorMessage(e));
    },
  });

  return (
    <section aria-labelledby="members-h" className="flex flex-col gap-3">
      <h2 id="members-h" className="text-lg">
        {t("listTitle")}
      </h2>
      {members.isPending ? (
        <div className="flex flex-col gap-2" aria-busy="true">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : members.isError ? (
        <ErrorState
          title={t("loadFailed")}
          description={errorMessage(members.error)}
          retryLabel={t("retry")}
          onRetry={() => void members.refetch()}
        />
      ) : (
        <ul className="m-0 flex list-none flex-col divide-y divide-border-1 rounded-card border border-border-1 bg-bg-1 p-0">
          {members.data.map((m) => (
            <li key={m.id} className="flex flex-wrap items-center gap-3 p-3">
              <div className="min-w-0 flex-1">
                <p className="m-0 truncate font-medium">{m.display_name}</p>
                <p className="m-0 truncate text-sm text-text-2">{m.email}</p>
              </div>
              {manage ? (
                <>
                  <Select
                    aria-label={t("roleOf", { name: m.display_name })}
                    className="w-40"
                    value={m.role}
                    disabled={changeRole.isPending}
                    onChange={(e) => changeRole.mutate({ id: m.id, role: e.target.value as Role })}
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {tr(r)}
                      </option>
                    ))}
                  </Select>
                  <Button
                    variant="ghost"
                    size="dense"
                    icon={<Trash2 size={16} aria-hidden />}
                    onClick={() => setRemoving(m)}
                  >
                    {t("remove")}
                  </Button>
                </>
              ) : (
                <Badge>{tr(m.role as Role)}</Badge>
              )}
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={t("removeTitle", { name: removing?.display_name ?? "" })}
        description={t("removeBody")}
        confirmLabel={t("removeConfirm")}
        cancelLabel={t("cancel")}
        closeLabel={t("close")}
        loading={remove.isPending}
        onConfirm={() => removing && remove.mutate(removing.id)}
      />
    </section>
  );
}

function Invitations({ orgId }: { orgId: string }) {
  const t = useTranslations("members");
  const tr = useTranslations("panel.roles");
  const errorMessage = useErrorMessage();
  const format = useFormatter();
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");

  const list = useQuery({
    queryKey: qk.invitations(orgId),
    queryFn: () => unwrap(api.GET("/api/v1/invitations")),
  });

  const invite = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/invitations", { body: { email: email.trim(), role } })),
    onSuccess: () => {
      toast.success(t("invited", { email: email.trim() }));
      setEmail("");
      return qc.invalidateQueries({ queryKey: qk.invitations(orgId) });
    },
  });

  const revoke = useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.DELETE("/api/v1/invitations/{invitation_id}", {
          params: { path: { invitation_id: id } },
        }),
      ),
    onSuccess: () => {
      toast.success(t("revoked"));
      return qc.invalidateQueries({ queryKey: qk.invitations(orgId) });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const server = fieldErrors(invite.error);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    invite.mutate();
  };

  return (
    <section aria-labelledby="inv-h" className="flex flex-col gap-3">
      <h2 id="inv-h" className="text-lg">
        {t("inviteTitle")}
      </h2>
      <form
        onSubmit={submit}
        className="flex flex-wrap items-start gap-3 rounded-card border border-border-1 bg-bg-1 p-4"
        noValidate
      >
        <Field label={t("inviteEmail")} className="min-w-64 flex-1" error={server.email}>
          {(a) => (
            <Input
              {...a}
              type="email"
              name="invite-email"
              inputMode="email"
              autoComplete="off"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          )}
        </Field>
        <Field label={t("inviteRole")} className="w-44">
          {(a) => (
            <Select {...a} value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {ROLES.filter((r) => r !== "owner").map((r) => (
                <option key={r} value={r}>
                  {tr(r)}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <div className="flex flex-col gap-1.5">
          <span aria-hidden className="text-sm">
            &nbsp;
          </span>
          <Button
            type="submit"
            variant="primary"
            icon={<MailPlus size={16} aria-hidden />}
            loading={invite.isPending}
            disabled={!email.trim()}
          >
            {t("inviteSend")}
          </Button>
        </div>
        {invite.isError && !server.email ? (
          <div className="w-full">
            <Notice tone="danger">{errorMessage(invite.error)}</Notice>
          </div>
        ) : null}
      </form>
      {list.isPending ? (
        <Skeleton className="h-12 w-full" />
      ) : list.isError ? (
        <ErrorState
          title={t("loadFailed")}
          description={errorMessage(list.error)}
          retryLabel={t("retry")}
          onRetry={() => void list.refetch()}
        />
      ) : list.data.length === 0 ? (
        <p className="m-0 text-sm text-text-2">{t("noInvitations")}</p>
      ) : (
        <ul className="m-0 flex list-none flex-col divide-y divide-border-1 rounded-card border border-border-1 bg-bg-1 p-0">
          {list.data.map((i) => (
            <li key={i.id} className="flex flex-wrap items-center gap-3 p-3">
              <div className="min-w-0 flex-1">
                <p className="m-0 truncate font-medium">{i.email}</p>
                <p className="m-0 text-sm text-text-2">
                  {t("expires", {
                    date: format.dateTime(new Date(i.expires_at), { dateStyle: "medium" }),
                  })}
                </p>
              </div>
              <Badge tone="info">{tr(i.role as Role)}</Badge>
              <Button
                variant="ghost"
                size="dense"
                loading={revoke.isPending && revoke.variables === i.id}
                onClick={() => revoke.mutate(i.id)}
              >
                {t("revoke")}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
