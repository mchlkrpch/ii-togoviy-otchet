import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "./styles.css";
import "./workbench.css";
import "./project.css";
import "./floating.css";
import "./explorer.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
