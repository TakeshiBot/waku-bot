/** Parse a date filter in the bot's UTC+7 timezone; explicit ISO offsets are respected. */
export function parseBotMoment(raw: string, endOfDay: boolean): string | undefined {
  const value = raw.trim().replace(" ", "T");
  if (!value) return undefined;
  const match =
    /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?(Z|[+-]\d{2}:\d{2})?)?$/.exec(
      value,
    );
  if (!match) return undefined;
  const [, year, month, day, hour, minute, second, , offset] = match;
  // Reject invalid calendar dates rather than letting Date normalize them into another month.
  const calendar = new Date(`${year}-${month}-${day}T00:00:00Z`);
  if (
    Number.isNaN(calendar.getTime()) ||
    calendar.getUTCFullYear() !== Number(year) ||
    calendar.getUTCMonth() + 1 !== Number(month) ||
    calendar.getUTCDate() !== Number(day) ||
    Number(hour ?? 0) > 23 ||
    Number(minute ?? 0) > 59 ||
    Number(second ?? 0) > 59
  )
    return undefined;
  const normalized =
    hour === undefined
      ? `${value}T${endOfDay ? "23:59:59.999" : "00:00:00"}+07:00`
      : `${value}${offset ? "" : "+07:00"}`;
  const moment = new Date(normalized);
  return Number.isNaN(moment.getTime()) ? undefined : moment.toISOString();
}
