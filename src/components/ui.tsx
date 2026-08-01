import { Ionicons } from "@expo/vector-icons";
import { router } from "expo-router";
import type { ComponentProps, PropsWithChildren, ReactNode } from "react";
import {
  ActivityIndicator,
  Image,
  Platform,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from "react-native";

import { imageFor } from "../lib/images";
import { money } from "../lib/format";
import { colors, radii, shadows, spacing } from "../theme";
import type { MembershipType, Product } from "../types";
import { useAuth } from "../store/AuthContext";
import { useCart } from "../store/CartContext";
import { useWorkspace } from "../store/WorkspaceContext";

type IconName = ComponentProps<typeof Ionicons>["name"];

export function AppViewport({ children }: PropsWithChildren) {
  return (
    <View style={styles.viewport}>
      <SafeAreaView style={styles.shell}>{children}</SafeAreaView>
    </View>
  );
}

type ScreenProps = PropsWithChildren<{
  scroll?: boolean;
  contentStyle?: StyleProp<ViewStyle>;
  bottom?: ReactNode;
}>;

export function Screen({
  children,
  scroll = true,
  contentStyle,
  bottom,
}: ScreenProps) {
  return (
    <View style={styles.screen}>
      {scroll ? (
        <ScrollView
          contentContainerStyle={[styles.screenContent, contentStyle]}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          {children}
        </ScrollView>
      ) : (
        <View style={[styles.screenContent, styles.flex, contentStyle]}>
          {children}
        </View>
      )}
      {bottom}
    </View>
  );
}

export function BrandLockup({ light = false }: { light?: boolean }) {
  return (
    <View>
      <View style={styles.brandRow}>
        <Text style={[styles.brandName, light && styles.textLight]}>
          十里方圓
        </Text>
        <Ionicons
          color={light ? "#E9C7A7" : colors.orange}
          name="leaf"
          size={19}
        />
      </View>
      <Text style={[styles.orgName, light && styles.orgNameLight]}>
        臺灣城鄉永續生活消費合作社
      </Text>
    </View>
  );
}

export function WorkspaceTopBar() {
  const { workspace, setWorkspace, lastRoute } = useWorkspace();
  const { itemCount } = useCart();
  const { isAuthenticated } = useAuth();

  const changeWorkspace = (next: "life" | "social") => {
    if (next === workspace) return;
    setWorkspace(next);
    router.replace(lastRoute[next] as never);
  };

  return (
    <View style={styles.workspaceHeader}>
      <View style={styles.workspaceHeaderTop}>
        <BrandLockup />
        <View style={styles.workspaceActions}>
          {isAuthenticated ? (
            <IconButton
              icon="notifications-outline"
              label="通知中心"
              onPress={() => router.push("/notifications")}
            />
          ) : null}
          <IconButton
            badge={itemCount}
            icon="basket-outline"
            label="購物車"
            onPress={() => router.push("/(tabs)/cart")}
          />
        </View>
      </View>
      <View accessibilityRole="tablist" style={styles.workspaceSwitch}>
        {[
          { value: "life" as const, label: "生活消費", icon: "storefront-outline" as const },
          { value: "social" as const, label: "社務系統", icon: "people-outline" as const },
        ].map((option) => {
          const selected = option.value === workspace;
          return (
            <Pressable
              accessibilityRole="tab"
              accessibilityState={{ selected }}
              key={option.value}
              onPress={() => changeWorkspace(option.value)}
              style={({ pressed }) => [
                styles.workspaceOption,
                selected && styles.workspaceOptionSelected,
                pressed && styles.buttonPressed,
              ]}
            >
              <Ionicons
                color={selected ? colors.white : colors.forest}
                name={option.icon}
                size={18}
              />
              <Text
                style={[
                  styles.workspaceOptionLabel,
                  selected && styles.workspaceOptionLabelSelected,
                ]}
              >
                {option.label}
              </Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

type PageHeaderProps = {
  eyebrow?: string;
  title: string;
  subtitle?: string;
  onBack?: () => void;
  right?: ReactNode;
};

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  onBack,
  right,
}: PageHeaderProps) {
  return (
    <View style={styles.pageHeader}>
      <View style={styles.pageHeaderTop}>
        {onBack ? (
          <IconButton icon="arrow-back" label="返回" onPress={onBack} />
        ) : null}
        <View style={styles.pageTitleCopy}>
          {eyebrow ? <Text style={styles.eyebrow}>{eyebrow}</Text> : null}
          <Text style={styles.pageTitle}>{title}</Text>
        </View>
        {right ?? (onBack ? <View style={styles.headerSpacer} /> : null)}
      </View>
      {subtitle ? <Text style={styles.pageSubtitle}>{subtitle}</Text> : null}
    </View>
  );
}

export function SectionHeader({
  title,
  action,
  onAction,
}: {
  title: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <View style={styles.sectionHeader}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {action && onAction ? (
        <Pressable onPress={onAction} style={styles.textAction}>
          <Text style={styles.textActionLabel}>{action}</Text>
          <Ionicons color={colors.orange} name="arrow-forward" size={16} />
        </Pressable>
      ) : null}
    </View>
  );
}

type ButtonProps = {
  label: string;
  onPress: () => void;
  icon?: IconName;
  variant?: "primary" | "secondary" | "quiet" | "danger";
  disabled?: boolean;
  loading?: boolean;
  compact?: boolean;
};

export function Button({
  label,
  onPress,
  icon,
  variant = "primary",
  disabled,
  loading,
  compact,
}: ButtonProps) {
  return (
    <Pressable
      accessibilityLabel={label}
      accessibilityRole="button"
      accessibilityState={{ disabled: Boolean(disabled || loading), busy: Boolean(loading) }}
      disabled={disabled || loading}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button,
        styles[`button_${variant}`],
        compact && styles.buttonCompact,
        (disabled || loading) && styles.buttonDisabled,
        pressed && styles.buttonPressed,
      ]}
    >
      {loading ? (
        <ActivityIndicator
          color={variant === "primary" ? colors.white : colors.forest}
          size="small"
        />
      ) : (
        <>
          {icon ? (
            <Ionicons
              color={
                variant === "primary"
                  ? colors.white
                  : variant === "danger"
                    ? colors.danger
                    : colors.forest
              }
              name={icon}
              size={18}
            />
          ) : null}
          <Text
            style={[
              styles.buttonLabel,
              styles[`buttonLabel_${variant}`],
            ]}
          >
            {label}
          </Text>
        </>
      )}
    </Pressable>
  );
}

export function IconButton({
  icon,
  label,
  onPress,
  badge,
}: {
  icon: IconName;
  label: string;
  onPress: () => void;
  badge?: number;
}) {
  return (
    <Pressable
      accessibilityLabel={label}
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => [
        styles.iconButton,
        pressed && styles.buttonPressed,
      ]}
    >
      <Ionicons color={colors.forest} name={icon} size={21} />
      {badge ? (
        <View style={styles.iconBadge}>
          <Text style={styles.iconBadgeText}>{Math.min(badge, 9)}</Text>
        </View>
      ) : null}
    </Pressable>
  );
}

export function StatusPill({
  label,
  tone = "neutral",
}: {
  label: string;
  tone?: "neutral" | "positive" | "warning" | "danger";
}) {
  return (
    <View style={[styles.pill, styles[`pill_${tone}`]]}>
      <View style={[styles.pillDot, styles[`pillDot_${tone}`]]} />
      <Text style={[styles.pillText, styles[`pillText_${tone}`]]}>
        {label}
      </Text>
    </View>
  );
}

export function ProgressBar({
  value,
  color = colors.orange,
}: {
  value: number;
  color?: string;
}) {
  return (
    <View style={styles.progressTrack}>
      <View
        style={[
          styles.progressFill,
          {
            backgroundColor: color,
            width: `${Math.max(2, Math.min(value, 100))}%`,
          },
        ]}
      />
    </View>
  );
}

export function ProductCard({
  product,
  membership,
  onPress,
  onAdd,
  compact = false,
}: {
  product: Product;
  membership: MembershipType;
  onPress: () => void;
  onAdd?: () => void;
  compact?: boolean;
}) {
  const price =
    membership === "member" ? product.member_price : product.nonmember_price;
  return (
    <CatalogCard
      badge={product.badge}
      compact={compact}
      imageKey={product.image_key ?? product.id}
      imageUrl={product.image_url}
      meta={`${product.origin ?? product.category}・每${product.unit}`}
      onAction={onAdd}
      onPress={onPress}
      price={money(price)}
      priceLabel={membership === "member" ? "社員價" : "一般價"}
      title={product.name}
    />
  );
}

export function CatalogCard({
  title,
  meta,
  imageKey,
  imageUrl,
  badge,
  price,
  priceLabel,
  secondary,
  progress,
  progressLabel,
  onPress,
  onAction,
  actionIcon = "add",
  compact = true,
}: {
  title: string;
  meta: string;
  imageKey?: string | null;
  imageUrl?: string | null;
  badge?: string;
  price?: string;
  priceLabel?: string;
  secondary?: ReactNode;
  progress?: number;
  progressLabel?: string;
  onPress: () => void;
  onAction?: () => void;
  actionIcon?: IconName;
  compact?: boolean;
}) {
  return (
    <Pressable
      accessibilityHint="查看詳細資料"
      accessibilityLabel={[title, meta, priceLabel, price]
        .filter(Boolean)
        .join("，")}
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => [
        styles.productCard,
        compact && styles.productCardCompact,
        pressed && styles.cardPressed,
      ]}
    >
      <View style={styles.productImageWrap}>
        <Image
          accessibilityLabel={`${title}的商品照片`}
          resizeMode="cover"
          source={imageFor(imageKey, imageUrl)}
          style={styles.productImage}
        />
        {badge ? (
          <View style={styles.productBadge}>
            <Text numberOfLines={1} style={styles.productBadgeText}>
              {badge}
            </Text>
          </View>
        ) : null}
      </View>
      <View style={styles.productCopy}>
        <Text numberOfLines={2} style={styles.productName}>
          {title}
        </Text>
        <Text numberOfLines={2} style={styles.productMeta}>
          {meta}
        </Text>
        {secondary}
        {typeof progress === "number" ? (
          <View style={styles.catalogProgress}>
            <ProgressBar value={progress} />
            {progressLabel ? (
              <Text numberOfLines={1} style={styles.catalogProgressLabel}>
                {progressLabel}
              </Text>
            ) : null}
          </View>
        ) : null}
        {price || onAction ? (
          <View style={styles.productFooter}>
            <View style={styles.catalogPriceCopy}>
              {priceLabel ? (
                <Text style={styles.priceLabel}>{priceLabel}</Text>
              ) : null}
              {price ? <Text style={styles.productPrice}>{price}</Text> : null}
            </View>
            {onAction ? (
              <Pressable
                accessibilityLabel={`${
                  actionIcon === "add" ? "加入購物車：" : "操作："
                }${title}`}
                accessibilityRole="button"
                onPress={(event) => {
                  event.stopPropagation();
                  onAction();
                }}
                style={({ pressed }) => [
                  styles.addButton,
                  pressed && styles.buttonPressed,
                ]}
              >
                <Ionicons color={colors.white} name={actionIcon} size={22} />
              </Pressable>
            ) : null}
          </View>
        ) : null}
      </View>
    </Pressable>
  );
}

export function QuantityControl({
  value,
  onChange,
  min = 1,
  max = 99,
}: {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
}) {
  return (
    <View
      accessibilityLabel={`數量 ${value}`}
      accessibilityRole="adjustable"
      accessibilityValue={{ min, max, now: value }}
      style={styles.quantity}
    >
      <Pressable
        accessibilityLabel="減少數量"
        accessibilityRole="button"
        accessibilityState={{ disabled: value <= min }}
        disabled={value <= min}
        onPress={() => onChange(Math.max(min, value - 1))}
        style={styles.quantityButton}
      >
        <Ionicons
          color={value <= min ? colors.sage : colors.forest}
          name="remove"
          size={19}
        />
      </Pressable>
      <Text style={styles.quantityValue}>{value}</Text>
      <Pressable
        accessibilityLabel="增加數量"
        accessibilityRole="button"
        accessibilityState={{ disabled: value >= max }}
        disabled={value >= max}
        onPress={() => onChange(Math.min(max, value + 1))}
        style={styles.quantityButton}
      >
        <Ionicons
          color={value >= max ? colors.sage : colors.forest}
          name="add"
          size={19}
        />
      </Pressable>
    </View>
  );
}

export function SegmentControl<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <ScrollView
      accessibilityRole="tablist"
      contentContainerStyle={styles.segmentRow}
      horizontal
      showsHorizontalScrollIndicator={false}
    >
      {options.map((option) => {
        const selected = value === option.value;
        return (
          <Pressable
            accessibilityLabel={option.label}
            accessibilityRole="tab"
            accessibilityState={{ selected }}
            key={option.value}
            onPress={() => onChange(option.value)}
            style={[
              styles.segment,
              selected && styles.segmentSelected,
            ]}
          >
            <Text
              style={[
                styles.segmentLabel,
                selected && styles.segmentLabelSelected,
              ]}
            >
              {option.label}
            </Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

export function InfoRow({
  icon,
  label,
  value,
}: {
  icon: IconName;
  label: string;
  value: string;
}) {
  return (
    <View style={styles.infoRow}>
      <View style={styles.infoIcon}>
        <Ionicons color={colors.forest} name={icon} size={18} />
      </View>
      <View style={styles.infoCopy}>
        <Text style={styles.infoLabel}>{label}</Text>
        <Text style={styles.infoValue}>{value}</Text>
      </View>
    </View>
  );
}

export function LoadingState({ label = "載入中" }: { label?: string }) {
  return (
    <View style={styles.state}>
      <ActivityIndicator color={colors.orange} size="small" />
      <Text style={styles.stateDescription}>{label}</Text>
    </View>
  );
}

export function EmptyState({
  icon = "leaf-outline",
  title,
  description,
  action,
  onAction,
}: {
  icon?: IconName;
  title: string;
  description: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <View style={styles.state}>
      <View style={styles.stateIcon}>
        <Ionicons color={colors.forest} name={icon} size={27} />
      </View>
      <Text style={styles.stateTitle}>{title}</Text>
      <Text style={styles.stateDescription}>{description}</Text>
      {action && onAction ? (
        <Button label={action} onPress={onAction} variant="secondary" />
      ) : null}
    </View>
  );
}

export function InlineMessage({
  text,
  tone = "warning",
}: {
  text: string;
  tone?: "warning" | "danger" | "positive";
}) {
  return (
    <View style={[styles.message, styles[`message_${tone}`]]}>
      <Ionicons
        color={tone === "danger" ? colors.danger : colors.forest}
        name={
          tone === "positive"
            ? "checkmark-circle-outline"
            : "information-circle-outline"
        }
        size={19}
      />
      <Text style={styles.messageText}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  viewport: {
    alignItems: "center",
    backgroundColor: Platform.OS === "web" ? "#D9DED5" : colors.cream,
    flex: 1,
  },
  shell: {
    backgroundColor: colors.cream,
    flex: 1,
    maxWidth: Platform.OS === "web" ? 520 : undefined,
    overflow: "hidden",
    width: "100%",
    ...Platform.select({
      web: {
        boxShadow: "0 20px 65px rgba(23,63,53,0.15)",
      },
    }),
  },
  screen: {
    backgroundColor: colors.cream,
    flex: 1,
  },
  screenContent: {
    paddingBottom: spacing.xl,
  },
  flex: { flex: 1 },
  brandRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 5,
  },
  brandName: {
    color: colors.forest,
    fontSize: 24,
    fontWeight: "900",
    letterSpacing: 2.5,
  },
  textLight: { color: colors.white },
  orgName: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 0.5,
    marginTop: 3,
  },
  orgNameLight: { color: "#DFE8E1" },
  pageHeader: {
    paddingHorizontal: spacing.md,
    paddingBottom: 12,
    paddingTop: spacing.md,
  },
  pageHeaderTop: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  pageTitleCopy: { flex: 1 },
  eyebrow: {
    color: colors.orange,
    fontSize: 12,
    fontWeight: "800",
    letterSpacing: 1.3,
    marginBottom: 3,
  },
  pageTitle: {
    color: colors.forest,
    fontSize: 27,
    fontWeight: "900",
    letterSpacing: 0.3,
  },
  pageSubtitle: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 20,
    marginTop: 7,
    maxWidth: 390,
  },
  headerSpacer: { width: 42 },
  sectionHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 12,
  },
  sectionTitle: {
    color: colors.forest,
    fontSize: 20,
    fontWeight: "900",
  },
  textAction: {
    alignItems: "center",
    flexDirection: "row",
    gap: 4,
    minHeight: 44,
    paddingVertical: 5,
  },
  textActionLabel: {
    color: colors.orange,
    fontSize: 12,
    fontWeight: "800",
  },
  button: {
    alignItems: "center",
    borderRadius: radii.md,
    flexDirection: "row",
    gap: 7,
    justifyContent: "center",
    minHeight: 50,
    paddingHorizontal: spacing.lg,
  },
  button_primary: { backgroundColor: colors.orange },
  button_secondary: {
    backgroundColor: colors.sageLight,
    borderColor: colors.sage,
    borderWidth: 1,
  },
  button_quiet: {
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderWidth: 1,
  },
  button_danger: {
    backgroundColor: colors.dangerSoft,
    borderColor: "#E4B7AA",
    borderWidth: 1,
  },
  buttonCompact: {
    minHeight: 44,
    paddingHorizontal: 14,
  },
  buttonDisabled: { opacity: 0.45 },
  buttonPressed: { opacity: 0.72, transform: [{ scale: 0.98 }] },
  buttonLabel: { fontSize: 15, fontWeight: "900" },
  buttonLabel_primary: { color: colors.white },
  buttonLabel_secondary: { color: colors.forest },
  buttonLabel_quiet: { color: colors.forest },
  buttonLabel_danger: { color: colors.danger },
  iconButton: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderRadius: 14,
    borderWidth: 1,
    height: 44,
    justifyContent: "center",
    marginRight: 10,
    width: 44,
  },
  iconBadge: {
    alignItems: "center",
    backgroundColor: colors.orange,
    borderRadius: 8,
    height: 16,
    justifyContent: "center",
    minWidth: 16,
    paddingHorizontal: 3,
    position: "absolute",
    right: -4,
    top: -4,
  },
  iconBadgeText: {
    color: colors.white,
    fontSize: 12,
    fontWeight: "900",
  },
  pill: {
    alignItems: "center",
    alignSelf: "flex-start",
    backgroundColor: colors.sageLight,
    borderRadius: radii.pill,
    flexDirection: "row",
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  pill_positive: { backgroundColor: colors.successSoft },
  pill_warning: { backgroundColor: colors.amberSoft },
  pill_danger: { backgroundColor: colors.dangerSoft },
  pill_neutral: { backgroundColor: colors.sageLight },
  pillDot: {
    backgroundColor: colors.moss,
    borderRadius: 3,
    height: 6,
    width: 6,
  },
  pillDot_positive: { backgroundColor: colors.success },
  pillDot_warning: { backgroundColor: colors.amber },
  pillDot_danger: { backgroundColor: colors.danger },
  pillDot_neutral: { backgroundColor: colors.moss },
  pillText: { color: colors.forest, fontSize: 12, fontWeight: "800" },
  pillText_positive: { color: colors.success },
  pillText_warning: { color: "#8A5C26" },
  pillText_danger: { color: colors.danger },
  pillText_neutral: { color: colors.forest },
  progressTrack: {
    backgroundColor: colors.creamDeep,
    borderRadius: radii.pill,
    height: 8,
    overflow: "hidden",
  },
  progressFill: { borderRadius: radii.pill, height: "100%" },
  productCard: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    overflow: "hidden",
    width: 205,
    ...shadows.card,
  },
  productCardCompact: { width: "48.3%" },
  cardPressed: { opacity: 0.82, transform: [{ translateY: 1 }] },
  productImageWrap: { aspectRatio: 4 / 3, backgroundColor: colors.sageLight },
  productImage: { height: "100%", width: "100%" },
  productBadge: {
    backgroundColor: "rgba(255,253,247,0.90)",
    borderRadius: radii.xs,
    left: 9,
    paddingHorizontal: 7,
    paddingVertical: 4,
    position: "absolute",
    top: 9,
  },
  productBadgeText: {
    color: colors.forest,
    fontSize: 12,
    fontWeight: "800",
  },
  productCopy: { padding: 13 },
  productName: {
    color: colors.forest,
    fontSize: 16,
    fontWeight: "900",
    lineHeight: 21,
    minHeight: 42,
  },
  productMeta: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17,
    marginTop: 4,
    minHeight: 34,
  },
  productFooter: {
    alignItems: "flex-end",
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 13,
  },
  priceLabel: { color: colors.muted, fontSize: 12 },
  productPrice: {
    color: colors.orange,
    fontSize: 20,
    fontWeight: "900",
    marginTop: 1,
  },
  addButton: {
    alignItems: "center",
    backgroundColor: colors.forest,
    borderRadius: 12,
    height: 44,
    justifyContent: "center",
    width: 44,
  },
  quantity: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderRadius: 13,
    borderWidth: 1,
    flexDirection: "row",
    height: 46,
  },
  quantityButton: {
    alignItems: "center",
    height: 44,
    justifyContent: "center",
    width: 40,
  },
  quantityValue: {
    color: colors.forest,
    fontSize: 15,
    fontWeight: "900",
    minWidth: 32,
    textAlign: "center",
  },
  segmentRow: { gap: 8, paddingHorizontal: spacing.md },
  segment: {
    alignItems: "center",
    borderColor: colors.line,
    borderRadius: radii.pill,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: 16,
    paddingVertical: 9,
  },
  segmentSelected: {
    backgroundColor: colors.forest,
    borderColor: colors.forest,
  },
  segmentLabel: { color: colors.muted, fontSize: 12, fontWeight: "800" },
  segmentLabelSelected: { color: colors.white },
  infoRow: { alignItems: "center", flexDirection: "row", gap: 12 },
  infoIcon: {
    alignItems: "center",
    backgroundColor: colors.sageLight,
    borderRadius: 12,
    height: 40,
    justifyContent: "center",
    width: 40,
  },
  infoCopy: { flex: 1 },
  infoLabel: { color: colors.muted, fontSize: 12 },
  infoValue: {
    color: colors.forest,
    fontSize: 13,
    fontWeight: "800",
    lineHeight: 19,
    marginTop: 2,
  },
  state: {
    alignItems: "center",
    gap: 10,
    justifyContent: "center",
    minHeight: 270,
    padding: spacing.xl,
  },
  stateIcon: {
    alignItems: "center",
    backgroundColor: colors.sageLight,
    borderRadius: 24,
    height: 58,
    justifyContent: "center",
    width: 58,
  },
  stateTitle: { color: colors.forest, fontSize: 19, fontWeight: "900" },
  stateDescription: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 20,
    maxWidth: 300,
    textAlign: "center",
  },
  message: {
    alignItems: "flex-start",
    backgroundColor: colors.amberSoft,
    borderRadius: radii.md,
    flexDirection: "row",
    gap: 9,
    padding: 12,
  },
  message_warning: { backgroundColor: colors.amberSoft },
  message_danger: { backgroundColor: colors.dangerSoft },
  message_positive: { backgroundColor: colors.successSoft },
  messageText: {
    color: colors.charcoal,
    flex: 1,
    fontSize: 12,
    lineHeight: 18,
  },
  workspaceHeader: {
    backgroundColor: colors.cream,
    borderBottomColor: colors.line,
    borderBottomWidth: 1,
    paddingBottom: 10,
    paddingHorizontal: spacing.md,
    paddingTop: 10,
  },
  workspaceHeaderTop: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  workspaceActions: {
    flexDirection: "row",
  },
  workspaceSwitch: {
    backgroundColor: colors.creamDeep,
    borderRadius: radii.md,
    flexDirection: "row",
    marginTop: 10,
    padding: 3,
  },
  workspaceOption: {
    alignItems: "center",
    borderRadius: 13,
    flex: 1,
    flexDirection: "row",
    gap: 7,
    justifyContent: "center",
    minHeight: 44,
  },
  workspaceOptionSelected: {
    backgroundColor: colors.forest,
  },
  workspaceOptionLabel: {
    color: colors.forest,
    fontSize: 13,
    fontWeight: "900",
  },
  workspaceOptionLabelSelected: {
    color: colors.white,
  },
  catalogProgress: {
    gap: 6,
    marginTop: 10,
  },
  catalogProgressLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700",
  },
  catalogPriceCopy: {
    flex: 1,
  },
});
