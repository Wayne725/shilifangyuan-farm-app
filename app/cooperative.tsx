import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router } from "expo-router";
import { useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { Button, EmptyState, InlineMessage, LoadingState, PageHeader, Screen, StatusPill } from "../src/components/ui";
import { api, getErrorMessage } from "../src/services/api";
import { colors, radii, spacing } from "../src/theme";

export default function CooperativeScreen() {
  const queryClient = useQueryClient();
  const education = useQuery({ queryKey: ["education"], queryFn: api.cooperativeEducation });
  const points = useQuery({ queryKey: ["my-points"], queryFn: api.myPoints });
  const badges = useQuery({ queryKey: ["my-badges"], queryFn: api.myBadges });
  const wishes = useQuery({ queryKey: ["wishes"], queryFn: api.wishes });
  const meetings = useQuery({ queryKey: ["meetings"], queryFn: api.meetings });
  const surplus = useQuery({ queryKey: ["my-surplus"], queryFn: api.mySurplusDistributions });
  const [attempt, setAttempt] = useState<Awaited<ReturnType<typeof api.startEducationAttempt>> | null>(null);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [wishName, setWishName] = useState("");
  const [wishDescription, setWishDescription] = useState("");
  const [message, setMessage] = useState("");

  const startQuiz = useMutation({ mutationFn: api.startEducationAttempt, onSuccess: setAttempt });
  const submitQuiz = useMutation({
    mutationFn: () => api.submitEducationAttempt(attempt!.attempt_id, answers),
    onSuccess: async (result) => {
      setMessage(result.passed ? "測驗通過，合作教育已完成。" : `本次 ${result.score} 分，請重新挑戰。`);
      setAttempt(null);
      setAnswers({});
      await queryClient.invalidateQueries({ queryKey: ["education"] });
    },
  });
  const createWish = useMutation({
    mutationFn: () => api.createWish({ name: wishName.trim(), description: wishDescription.trim() }),
    onSuccess: async () => {
      setWishName("");
      setWishDescription("");
      await queryClient.invalidateQueries({ queryKey: ["wishes"] });
    },
  });
  const supportWish = useMutation({
    mutationFn: api.supportWish,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["wishes"] }),
  });
  const error = education.error ?? points.error ?? wishes.error ?? startQuiz.error ?? submitQuiz.error ?? createWish.error ?? supportWish.error;

  if (education.isLoading || points.isLoading) return <LoadingState label="載入合作參與資料" />;

  return (
    <Screen>
      <PageHeader onBack={() => router.back()} subtitle="消費不是持股；參與、學習與共同決策才是合作社的核心。" title="我的合作參與" />
      <View style={styles.content}>
        {message ? <InlineMessage text={message} tone="positive" /> : null}
        {error ? <InlineMessage text={getErrorMessage(error)} tone="danger" /> : null}

        <View style={styles.metric}><Text style={styles.metricLabel}>目前積點</Text><Text style={styles.metricValue}>{points.data?.balance ?? 0}</Text></View>
        {(badges.data ?? []).map((badge) => <View key={badge.key} style={styles.row}><Text style={styles.title}>{badge.name}</Text><Text style={styles.meta}>{badge.description}</Text></View>)}

        <View style={styles.section}>
          <View style={styles.heading}><Text style={styles.headingText}>合作教育</Text><StatusPill label={education.data?.passed ? "已完成" : "未完成"} tone={education.data?.passed ? "positive" : "warning"} /></View>
          {education.data?.lectures.map((lecture) => <View key={lecture.id} style={styles.row}><Text style={styles.title}>{lecture.position}. {lecture.title}</Text><Text style={styles.body}>{lecture.body}</Text></View>)}
          {!education.data?.passed && !attempt ? <Button label="隨機抽 3 題測驗" loading={startQuiz.isPending} onPress={() => startQuiz.mutate()} /> : null}
          {attempt?.questions.map((question) => (
            <View key={question.id} style={styles.quiz}>
              <Text style={styles.title}>{question.prompt}</Text>
              {question.options.map((option, index) => <Pressable key={option} onPress={() => setAnswers((current) => ({ ...current, [question.id]: index }))} style={[styles.option, answers[question.id] === index && styles.optionSelected]}><Text style={styles.body}>{option}</Text></Pressable>)}
            </View>
          ))}
          {attempt ? <Button disabled={Object.keys(answers).length !== attempt.questions.length} label="送出答案" loading={submitQuiz.isPending} onPress={() => submitQuiz.mutate()} /> : null}
        </View>

        <View style={styles.section}>
          <Text style={styles.headingText}>願望清單</Text>
          <TextInput placeholder="想引進的新商品" placeholderTextColor={colors.sage} style={styles.input} value={wishName} onChangeText={setWishName} />
          <TextInput multiline placeholder="為什麼需要它、參考來源或期待價格" placeholderTextColor={colors.sage} style={[styles.input, styles.multiline]} value={wishDescription} onChangeText={setWishDescription} />
          <Button disabled={!wishName.trim() || !wishDescription.trim()} label="提交願望" loading={createWish.isPending} onPress={() => createWish.mutate()} />
          {(wishes.data ?? []).map((wish) => <View key={wish.id} style={styles.row}><View style={styles.heading}><Text style={styles.title}>{wish.name}</Text><StatusPill label={wish.status} tone={wish.status === "launched" ? "positive" : "neutral"} /></View><Text style={styles.body}>{wish.description}</Text><Button compact disabled={wish.supported_by_me} label={wish.supported_by_me ? `已集氣 ${wish.support_count}` : `幫忙集氣 ${wish.support_count}`} onPress={() => supportWish.mutate(wish.id)} variant="quiet" /></View>)}
        </View>

        <View style={styles.section}><Text style={styles.headingText}>會議</Text>{(meetings.data ?? []).map((meeting) => <View key={meeting.id} style={styles.row}><Text style={styles.title}>{meeting.title}</Text><Text style={styles.meta}>{new Date(meeting.starts_at).toLocaleString("zh-TW")} · 出席率 {(meeting.attendance_rate * 100).toFixed(1)}%</Text></View>)}{!meetings.data?.length ? <EmptyState title="目前沒有會議" description="管理員建立會議後會顯示在這裡。" /> : null}</View>
        <View style={styles.section}><Text style={styles.headingText}>我的結餘分配</Text>{(surplus.data ?? []).map((item) => <View key={item.fiscal_year_id} style={styles.row}><Text style={styles.title}>{item.label}</Text><Text style={styles.meta}>消費貢獻 ${item.contribution_amount.toLocaleString()} · 分配 ${item.distribution_amount.toLocaleString()}</Text></View>)}{!surplus.data?.length ? <Text style={styles.meta}>尚無已確認的年度分配。</Text> : null}</View>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 16, padding: spacing.md },
  section: { backgroundColor: colors.paper, borderRadius: radii.lg, gap: 12, padding: spacing.md },
  heading: { alignItems: "center", flexDirection: "row", justifyContent: "space-between" },
  headingText: { color: colors.forest, fontSize: 20, fontWeight: "900" },
  row: { borderBottomColor: colors.line, borderBottomWidth: 1, gap: 5, paddingVertical: 10 },
  title: { color: colors.charcoal, fontSize: 14, fontWeight: "800" },
  body: { color: colors.muted, fontSize: 13, lineHeight: 20 },
  meta: { color: colors.muted, fontSize: 12 },
  metric: { backgroundColor: colors.forest, borderRadius: radii.lg, padding: spacing.lg },
  metricLabel: { color: colors.white, fontSize: 13 },
  metricValue: { color: colors.white, fontSize: 38, fontWeight: "900" },
  input: { backgroundColor: colors.cream, borderColor: colors.line, borderRadius: radii.md, borderWidth: 1, color: colors.charcoal, minHeight: 48, padding: 12 },
  multiline: { minHeight: 90, textAlignVertical: "top" },
  quiz: { gap: 8 },
  option: { borderColor: colors.line, borderRadius: radii.md, borderWidth: 1, padding: 11 },
  optionSelected: { backgroundColor: colors.cream, borderColor: colors.forest },
});
