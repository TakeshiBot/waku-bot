<script setup lang="ts">
import { ref, watch } from "vue";

import { fetchQuotaSettings, saveQuotaSettings } from "@/api/endpoints/admin";
import { isApiError } from "@/api/errors";
import NumberField from "@/components/NumberField.vue";
import SettingsRow from "@/components/SettingsRow.vue";
import SettingsSection from "@/components/SettingsSection.vue";
import StateBlock from "@/components/StateBlock.vue";
import { useAsyncData } from "@/composables/useAsyncData";
import { useNotice } from "@/composables/useNotice";
import { t, tError } from "@/i18n";
import { haptics } from "@/telegram";

const props = defineProps<{ disabled?: boolean }>();
const emit = defineEmits<{ updated: [] }>();
const settings = useAsyncData((signal) => fetchQuotaSettings(signal));
const amount = ref(0);
const saving = ref(false);
const { notify, notifyError } = useNotice();
watch(
  () => settings.data.value,
  (value) => {
    if (value) amount.value = value.free_daily_tokens;
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
  saving.value = true;
  try {
    settings.data.value = await saveQuotaSettings(amount.value);
    emit("updated");
    notify(t("app.saved"));
    haptics.success();
  } catch (error) {
    notifyError(isApiError(error) ? error.message || tError(error.code) : t("app.loadFailed"));
    haptics.error();
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <SettingsSection :label="t('quota.title')">
    <StateBlock
      :loading="settings.loading.value"
      :error="settings.error.value"
      @retry="settings.reload"
    >
      <NumberField
        v-model="amount"
        :label="t('quota.dailyFree')"
        :hint="t('quota.dailyHint')"
        :min="0"
        :max="1000000000000"
        :disabled="disabled || saving"
      />
      <SettingsRow
        v-if="!disabled"
        :label="t('app.save')"
        navigable
        :busy="saving"
        :disabled="
          amount === settings.data.value?.free_daily_tokens ||
          amount < 0 ||
          amount > 1000000000000 ||
          !Number.isSafeInteger(amount)
        "
        @click="save"
      />
    </StateBlock>
  </SettingsSection>
</template>
