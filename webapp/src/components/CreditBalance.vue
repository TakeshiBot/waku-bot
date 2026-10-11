<script setup lang="ts">
import { ref, watch } from "vue";

import { setChatCredit } from "@/api/endpoints/admin";
import { isApiError } from "@/api/errors";
import type { AgentUsage } from "@/api/types";
import NumberField from "@/components/NumberField.vue";
import SettingsRow from "@/components/SettingsRow.vue";
import { useMainButton } from "@/composables/useMainButton";
import { useNotice } from "@/composables/useNotice";
import { t, tError } from "@/i18n";
import { haptics } from "@/telegram";
import { formatTokens } from "@/utils/format";

const props = defineProps<{
  chatId: number;
  balance: number;
  disabled?: boolean;
}>();
const emit = defineEmits<{ updated: [quota: AgentUsage] }>();
const amount = ref(props.balance);
const saving = ref(false);
const { notify, notifyError } = useNotice();
watch(
  () => [props.chatId, props.balance] as const,
  ([chatId, balance], [previousId, previousBalance]) => {
    if (chatId !== previousId || amount.value === previousBalance) amount.value = balance;
  },
);

async function save(): Promise<void> {
  if (
    props.disabled ||
    saving.value ||
    !Number.isSafeInteger(amount.value) ||
    amount.value < 0 ||
    amount.value > 1000000000000
  )
    return;
  const chatId = props.chatId;
  saving.value = true;
  try {
    const quota = await setChatCredit(chatId, amount.value);
    if (props.chatId !== chatId) return;
    amount.value = quota.credits;
    emit("updated", quota);
    notify(t("app.saved"));
    haptics.success();
  } catch (error) {
    if (props.chatId !== chatId) return;
    notifyError(isApiError(error) ? error.message || tError(error.code) : t("app.loadFailed"));
    haptics.error();
  } finally {
    saving.value = false;
  }
}

useMainButton({
  text: () => t("app.save"),
  visible: () => !props.disabled && amount.value !== props.balance,
  enabled: () =>
    !props.disabled &&
    !saving.value &&
    Number.isSafeInteger(amount.value) &&
    amount.value >= 0 &&
    amount.value <= 1000000000000,
  loading: () => saving.value,
  onClick: () => void save(),
});
</script>

<template>
  <NumberField
    v-if="!disabled"
    v-model="amount"
    :label="t('quota.creditBalance')"
    :min="0"
    :max="1000000000000"
    :disabled="saving"
    :changed="amount !== balance"
  />
  <SettingsRow v-else :label="t('quota.creditBalance')" :value="formatTokens(balance)" />
</template>
