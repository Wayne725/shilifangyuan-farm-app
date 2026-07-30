import type { ImageSourcePropType } from "react-native";

const images: Record<string, ImageSourcePropType> = {
  tomatoes: require("../../assets/products/tomatoes.jpg"),
  "bok-choy": require("../../assets/products/bok-choy.jpg"),
  rice: require("../../assets/products/rice.jpg"),
  eggs: require("../../assets/products/eggs.jpg"),
  "fruit-corn": require("../../assets/products/fruit-corn.jpg"),
  "sweet-potato": require("../../assets/products/sweet-potato.jpg"),
  "pineapple-jam": require("../../assets/products/pineapple-jam.jpg"),
  "black-bean-soy-sauce": require("../../assets/products/black-bean-soy-sauce.jpg"),
  "generic-product": require("../../assets/products/generic-product.jpg"),
  "meal-lunchbox": require("../../assets/meals/taiwanese-lunchbox.png"),
  "member-hike": require("../../assets/community/member-hike.png"),
};

export function imageFor(key?: string | null, remoteUrl?: string | null) {
  if (remoteUrl?.startsWith("/assets/products/")) {
    const filename = remoteUrl.split("/").pop()?.replace(/\.[^.]+$/, "");
    return (
      images[filename ?? ""] ??
      images[key ?? ""] ??
      images["generic-product"]!
    );
  }
  if (remoteUrl) return { uri: remoteUrl };
  return images[key ?? ""] ?? images["generic-product"]!;
}
