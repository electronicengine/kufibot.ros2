import React, { useEffect, useRef } from "react";
import { Modal, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { WebView } from "react-native-webview";
import { Robot } from "./discovery";
import { State } from "./useRobot";

type Props = {
  visible: boolean;
  robot: Robot;
  state: State | null;
  error: string;
  send: (value: { type: string; [key: string]: unknown }) => void;
  onClose: () => void;
};
const commands = new Set(["claim", "mode", "playMimic", "stopMimic", "stop"]);
export function MimicEditor({
  visible,
  robot,
  state,
  error,
  send,
  onClose,
}: Props) {
  const web = useRef<WebView>(null);
  const origin = `http://${robot.host}:${robot.port}`;
  const publish = () =>
    web.current?.injectJavaScript(
      `window.kufibotMimicState?.(${JSON.stringify(state)});true;`,
    );
  useEffect(() => {
    if (visible) publish();
  }, [state, visible]);
  if (!visible) return null;
  return (
    <Modal visible animationType="slide" onRequestClose={onClose}>
      <SafeAreaView style={{ flex: 1, backgroundColor: "#101721" }}>
        {!!error && (
          <Text style={{ color: "#ffb0a0", padding: 8 }}>{error}</Text>
        )}
        <WebView
          ref={web}
          source={{ uri: `${origin}/mimics?native=1` }}
          onLoadEnd={publish}
          originWhitelist={[origin]}
          onShouldStartLoadWithRequest={(request) =>
            request.url.startsWith(`${origin}/`)
          }
          onMessage={(event) => {
            try {
              const value = JSON.parse(event.nativeEvent.data);
              if (value.type === "mimicClose") onClose();
              if (
                value.type === "mimicCommand" &&
                commands.has(value.command?.type)
              )
                send(value.command);
            } catch {
              /* Invalid bridge payloads never reach the controller. */
            }
          }}
          renderError={() => (
            <View style={{ padding: 20 }}>
              <Text style={{ color: "#ffb0a0" }}>
                Mimik editörü yüklenemedi. Robot bağlantısını kontrol edin.
              </Text>
            </View>
          )}
          style={{ flex: 1, backgroundColor: "#101721" }}
        />
      </SafeAreaView>
    </Modal>
  );
}
