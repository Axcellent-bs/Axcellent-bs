/** 바깥 값으로 받은 링크 중 http·https만 연다. javascript:·data: 같은 주소나 빈 값은 null */
export const safeLink = (url: string | null | undefined): string | null => {
  const u = url?.trim();
  return u && /^https?:\/\//i.test(u) ? u : null;
};
