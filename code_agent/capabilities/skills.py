# code_agent/capabilities/skills.py

from pathlib import Path

from code_agent.tools.registry import Tool, ToolRegistry


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)

    if len(parts) < 3:
        return {}, text

    meta = {}

    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"').strip("'")

    return meta, parts[2].strip()


class SkillRegistry:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills: dict[str, dict] = {}

    def scan(self):
        self.skills.clear()

        if not self.skills_dir.exists():
            return

        for directory in sorted(self.skills_dir.iterdir()):
            if not directory.is_dir():
                continue

            manifest = directory / "SKILL.md"

            if not manifest.exists():
                continue

            raw = manifest.read_text(encoding="utf-8")
            meta, _ = parse_frontmatter(raw)

            name = meta.get("name", directory.name)
            desc = meta.get("description", raw.split("\n")[0].lstrip("#").strip())

            self.skills[name] = {
                "name": name,
                "description": desc,
                "content": raw,
            }

    def list_skills(self) -> str:
        if not self.skills:
            return "(no skills found)"

        return "\n".join(
            f"- {skill['name']}: {skill['description']}"
            for skill in self.skills.values()
        )

    def load_skill(self, name: str) -> str:
        skill = self.skills.get(name)

        if not skill:
            available = ", ".join(self.skills.keys()) or "(none)"
            return f"Skill not found: {name}. Available: {available}"

        return skill["content"]


def register_skill_tools(registry: ToolRegistry, skills: SkillRegistry):
    registry.register(Tool(
        name="load_skill",
        description="Load the full content of a skill by name.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
        handler=skills.load_skill,
    ))