<script setup lang="ts">
import { ref, watch } from "vue";

import { grantCredit } from "@/api/endpoints/admin";
import { isApiError } from "@/api/errors";
import type { AgentUsage } from "@/api/types";
import NumberField from "@/components/NumberField.vue";
import SettingsRow from "@/components/SettingsRow.vue";
import { useNotice } from "@/composables/useNotice";
import { t, tError } from "@/i18n";
import { haptics } from "@/telegram";
import { formatTokens } from "@/utils/format";

const props = defineProps<{
  scope: "user" | "chat";
  accountId: number;
  balance: number;
  disabled?: boolean;
}>();
const emit = defineEmits<{ updated: [quota: AgentUsage] }>();
const amount = ref(0);
const saving = ref(false);
const { notify, notifyError } = useNotice();
// Retain the operation ID after an error: a response can be lost after commit.
let operation: { amount: number; id: string } | null = null;
watch(
  () => [props.scope, props.accountId],
  () => {
    operation = null;
    amount.value = 0;
  },
);

async function add(): Promise<void> {
  if (
    saving.value ||
    props.disabled ||
    !Number.isSafeInteger(amount.value) ||
    amount.value <= 0 ||
    amount.value > 1000000000000
  )
    return;
  if (!operation || operation.amount !== amount.value) {
    operation = { amount: amount.value, id: crypto.randomUUID() };
  }
  const pending = operation;
  const scope = props.scope;
  const accountId = props.accountId;
  saving.value = true;
  try {
    const result = await grantCredit(scope, accountId, pending.amount, pending.id);
    if (props.scope !== scope || props.accountId !== accountId) return;
    emit("updated", result.quota);
    notify(t("quota.creditAdded", { amount: formatTokens(pending.amount) }));
    amount.value = 0;
    operation = null;
    haptics.success();
  } catch (error) {
    if (props.scope !== scope || props.accountId !== accountId) return;
    notifyError(isApiError(error) ? error.message || tError(error.code) : t("app.loadFailed"));
    haptics.error();
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <SettingsRow :label="t('quota.creditBalance')" :value="formatTokens(balance)" />
  <NumberField
    v-if="!disabled"
    v-model="amount"
    :label="t('quota.creditAdd')"
    :hint="scope === 'chat' ? t('quota.groupCreditHint') : t('quota.creditHint')"
    :min="0"
    :max="1000000000000"
    :disabled="saving"
  />
  <SettingsRow
    v-if="!disabled"
    :label="t('quota.add')"
    :disabled="amount <= 0 || amount > 1000000000000 || !Number.isSafeInteger(amount)"
    :busy="saving"
    navigable
    @click="add"
  />
</template>
