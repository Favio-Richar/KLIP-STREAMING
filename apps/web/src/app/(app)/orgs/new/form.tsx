"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Field, Input, Notice } from "@klick/ui";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { api, qk, unwrap } from "@/lib/api";
import { slugify } from "@/lib/nav";
import { useOrgStore } from "@/lib/stores";
import { fieldErrors, useErrorMessage } from "@/lib/use-error-message";

export function NewOrgForm() {
  const t = useTranslations("org.create");
  const errorMessage = useErrorMessage();
  const router = useRouter();
  const qc = useQueryClient();
  const setOrgId = useOrgStore((s) => s.setOrgId);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);

  const create = useMutation({
    mutationFn: () => {
      const finalSlug = slug || slugify(name);
      return unwrap(
        api.POST("/api/v1/orgs", {
          body: { name: name.trim(), ...(finalSlug ? { slug: finalSlug } : {}) },
        }),
      );
    },
    onSuccess: async (org) => {
      setOrgId(org.id);
      await qc.invalidateQueries({ queryKey: qk.orgs });
      router.replace("/");
    },
  });

  const server = fieldErrors(create.error);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-6">
      <div className="flex flex-col gap-1.5">
        <h1 className="text-2xl">{t("title")}</h1>
        <p className="m-0 text-text-2">{t("subtitle")}</p>
      </div>
      <form
        onSubmit={submit}
        className="flex flex-col gap-4 rounded-card border border-border-1 bg-bg-1 p-5"
        noValidate
      >
        <Field label={t("name")} error={server.name}>
          {(a) => (
            <Input
              {...a}
              name="name"
              required
              maxLength={80}
              autoFocus
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (!slugEdited) setSlug(slugify(e.target.value));
              }}
            />
          )}
        </Field>
        <Field label={t("slug")} hint={t("slugHint")} error={server.slug}>
          {(a) => (
            <Input
              {...a}
              name="slug"
              maxLength={40}
              className="font-mono"
              value={slug}
              onChange={(e) => {
                setSlugEdited(true);
                setSlug(e.target.value.toLowerCase());
              }}
            />
          )}
        </Field>
        {create.isError && !server.name && !server.slug ? (
          <Notice tone="danger">{errorMessage(create.error)}</Notice>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button onClick={() => router.back()}>{t("cancel")}</Button>
          <Button
            type="submit"
            variant="primary"
            loading={create.isPending}
            disabled={!name.trim()}
          >
            {t("submit")}
          </Button>
        </div>
      </form>
    </div>
  );
}
