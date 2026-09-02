import { Github } from "lucide-react";
import { useLanguage } from "../lib/language";

export function GithubButton({ className = "" }: { className?: string }) {
  const { text } = useLanguage();
  const label = text("查看 GitHub 项目仓库", "View the GitHub repository");
  return <a className={`button button-secondary !h-10 !min-h-10 !w-10 !p-0 ${className}`} href="https://github.com/czh1302/paper-research" target="_blank" rel="noreferrer" title={label} aria-label={label}><Github className="h-4 w-4"/></a>;
}
