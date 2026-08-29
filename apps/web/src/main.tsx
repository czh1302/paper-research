import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import App from "./App";
import { LanguageProvider } from "./lib/language";
import { ThemeProvider } from "./lib/theme";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <HashRouter>
      <LanguageProvider><ThemeProvider><App /></ThemeProvider></LanguageProvider>
    </HashRouter>
  </React.StrictMode>,
);
