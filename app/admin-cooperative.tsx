import { useMutation, useQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import { useMemo, useState } from "react";
import { StyleSheet, Text, TextInput, View } from "react-native";

import { Button, EmptyState, InlineMessage, LoadingState, PageHeader, Screen, StatusPill } from "../src/components/ui";
import { api, getErrorMessage } from "../src/services/api";
import { useAuth } from "../src/store/AuthContext";
import { colors, radii, spacing } from "../src/theme";

const currentYear = new Date().getFullYear();

export default function AdminCooperativeScreen() {
  const { isAdmin } = useAuth();
  const [startsOn, setStartsOn] = useState(`${currentYear}-01-01`);
  const [endsOn, setEndsOn] = useState(`${currentYear}-12-31`);
  const [label, setLabel] = useState(`${currentYear} 年度`);
  const [cost, setCost] = useState("0");
  const [reserve, setReserve] = useState("50");
  const [message, setMessage] = useState("");
  const periodValid = /^\d{4}-\d{2}-\d{2}$/.test(startsOn) && /^\d{4}-\d{2}-\d{2}$/.test(endsOn);
  const nonmember = useQuery({ queryKey: ["nonmember-sales", startsOn, endsOn], queryFn: () => api.adminNonmemberSales(startsOn, endsOn), enabled: isAdmin && periodValid });
  const tax = useQuery({ queryKey: ["tax-ledger", startsOn, endsOn], queryFn: () => api.adminTaxLedger(startsOn, endsOn), enabled: isAdmin && periodValid });
  const body = useMemo(() => ({ label: label.trim(), starts_on: startsOn, ends_on: endsOn, total_cost: Number(cost), reserve_percentage: Number(reserve) }), [label, startsOn, endsOn, cost, reserve]);
  const dryRun = useMutation({ mutationFn: () => api.adminSurplusDryRun(body) });
  const confirm = useMutation({ mutationFn: () => api.adminConfirmSurplus(body), onSuccess: () => setMessage("年度結餘已確認撥付；此動作不可逆，稽核紀錄已建立。") });
  const error = nonmember.error ?? tax.error ?? dryRun.error ?? confirm.error;

  if (!isAdmin) return <EmptyState action="返回登入" description="此頁只開放管理員。" onAction={() => router.replace("/login")} title="需要管理權限" />;
  return (
    <Screen>
      <PageHeader onBack={() => router.back()} subtitle="統計與警示不會自行阻擋交易；政策待合作社正式決議。" title="財務與制度核心" />
      <View style={styles.content}>
        {message ? <InlineMessage text={message} tone="positive" /> : null}
        {error ? <InlineMessage text={getErrorMessage(error)} tone="danger" /> : null}
        <View style={styles.section}>
          <Text style={styles.heading}>查詢期間</Text>
          <View style={styles.row}><TextInput style={styles.input} value={startsOn} onChangeText={setStartsOn} /><Text style={styles.meta}>至</Text><TextInput style={styles.input} value={endsOn} onChangeText={setEndsOn} /></View>
        </View>
        {nonmember.isLoading ? <LoadingState label="計算非社員銷售" /> : nonmember.data ? <View style={styles.section}><View style={styles.row}><Text style={styles.heading}>非社員銷售上限</Text><StatusPill label={nonmember.data.level === "normal" ? "正常" : nonmember.data.level === "warning" ? "25% 預警" : "30% 警示"} tone={nonmember.data.level === "normal" ? "positive" : "warning"} /></View><Text style={styles.big}>{(nonmember.data.ratio * 100).toFixed(2)}%</Text><Text style={styles.meta}>非社員 ${nonmember.data.nonmember_revenue.toLocaleString()}／總營收 ${nonmember.data.total_revenue.toLocaleString()}；距 30% 尚有 ${nonmember.data.headroom_amount.toLocaleString()} 元緩衝。</Text><InlineMessage text="系統只統計與通知，不會阻擋交易。" tone="warning" /></View> : null}
        <View style={styles.section}><Text style={styles.heading}>稅務分類帳</Text>{(tax.data?.rows ?? []).map((item, index) => <View key={`${item.tax_type}-${item.membership_type}-${item.sales_channel}-${index}`} style={styles.ledger}><Text style={styles.title}>{item.tax_type} · {item.membership_type} · {item.sales_channel}</Text><Text style={styles.meta}>銷售 ${item.sales_amount.toLocaleString()}　稅額 ${item.tax_amount.toLocaleString()}　{item.order_count} 筆</Text></View>)}{!tax.data?.rows.length ? <Text style={styles.meta}>此期間沒有已付款未退款訂單。</Text> : null}</View>
        <View style={styles.section}>
          <Text style={styles.heading}>年度結餘分配</Text>
          <TextInput placeholder="會計年度名稱" style={styles.fullInput} value={label} onChangeText={setLabel} />
          <View style={styles.row}><TextInput keyboardType="number-pad" placeholder="總成本" style={styles.input} value={cost} onChangeText={setCost} /><TextInput keyboardType="number-pad" placeholder="公積金 %" style={styles.input} value={reserve} onChangeText={setReserve} /></View>
          <Button disabled={!periodValid || !label.trim() || Number(cost) < 0 || Number(reserve) < 0 || Number(reserve) > 100} label="試算（不寫入）" loading={dryRun.isPending} onPress={() => dryRun.mutate()} variant="secondary" />
          {dryRun.data ? <View style={styles.preview}><Text style={styles.title}>營收 ${dryRun.data.total_revenue.toLocaleString()} · 結餘 ${dryRun.data.total_surplus.toLocaleString()}</Text><Text style={styles.meta}>公積金 ${dryRun.data.reserve_amount.toLocaleString()} · 可分配 ${dryRun.data.distributable_surplus.toLocaleString()}</Text>{dryRun.data.distributions.map((item) => <Text key={item.member_id} style={styles.meta}>{item.member_name}：貢獻 ${item.contribution_amount.toLocaleString()} → 分配 ${item.distribution_amount.toLocaleString()}</Text>)}<Button label="確認撥付（不可逆）" loading={confirm.isPending} onPress={() => confirm.mutate()} /></View> : null}
        </View>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 14, padding: spacing.md },
  section: { backgroundColor: colors.paper, borderRadius: radii.lg, gap: 10, padding: spacing.md },
  heading: { color: colors.forest, fontSize: 19, fontWeight: "900" },
  row: { alignItems: "center", flexDirection: "row", gap: 8, justifyContent: "space-between" },
  input: { backgroundColor: colors.cream, borderRadius: radii.md, color: colors.charcoal, flex: 1, minHeight: 46, padding: 11 },
  fullInput: { backgroundColor: colors.cream, borderRadius: radii.md, color: colors.charcoal, minHeight: 46, padding: 11 },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 19 },
  big: { color: colors.forest, fontSize: 36, fontWeight: "900" },
  ledger: { borderBottomColor: colors.line, borderBottomWidth: 1, gap: 4, paddingVertical: 8 },
  title: { color: colors.charcoal, fontSize: 14, fontWeight: "800" },
  preview: { backgroundColor: colors.cream, borderRadius: radii.md, gap: 7, padding: 12 },
});
