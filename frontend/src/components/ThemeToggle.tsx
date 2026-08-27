import { useTheme } from "../theme/ThemeContext";
import { Button } from "./Button";

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();

  return (
    <Button variant="ghost" onClick={toggleTheme} className="w-full justify-center">
      {theme === "dark" ? "☀ Light mode" : "☾ Dark mode"}
    </Button>
  );
}
