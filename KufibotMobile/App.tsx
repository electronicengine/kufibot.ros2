import React from 'react';
import {SafeAreaProvider} from 'react-native-safe-area-context';
import MobileApp from './src/MobileApp';

export default function App() {
  return <SafeAreaProvider><MobileApp/></SafeAreaProvider>;
}
