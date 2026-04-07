/**
 * Configuration for linear-agent system.
 * Loads .env via dotenv (from CLI entry) for LINEAR_API_KEY.
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { z } from "zod";
const ConfigSchema = z.object({
    linear: z.object({
        apiKeyEnv: z.string().default("LINEAR_API_KEY"),
        teamId: z.string().optional(),
        teamKey: z.string().optional().describe("Team key e.g. ELM for Elmundi (used to create labels)"),
        defaultProject: z.string().optional(),
    }),
    workflow: z.object({
        roles: z.array(z.string()).default([
            "ba",
            "bug-agent",
            "architect",
            "qa-architect",
            "developer",
            "qa-automation",
            "release-manager",
        ]),
        skipRules: z
            .array(z.object({
            label: z.string(),
            skipRole: z.string(),
        }))
            .default([{ label: "flow:no-ba", skipRole: "ba" }]),
        labels: z.object({
            readyPrefix: z.string().default("ready:"),
            stagePrefix: z.string().default("stage:"),
            resultPrefix: z.string().default("result:"),
        }),
    }),
    release: z.object({
        requirePreview: z.boolean().default(true),
        requireHumanMerge: z.boolean().default(true),
        requireQaPass: z.boolean().default(true),
    }),
    qaBrowser: z.object({
        provider: z.enum(["playwright"]).default("playwright"),
        headless: z.boolean().default(true),
        artifactsDir: z.string().default("./artifacts"),
    }),
});
const DEFAULT_CONFIG = {
    linear: { apiKeyEnv: "LINEAR_API_KEY" },
    workflow: {
        roles: ["ba", "bug-agent", "architect", "qa-architect", "developer", "qa-automation", "release-manager"],
        skipRules: [{ label: "flow:no-ba", skipRole: "ba" }],
        labels: { readyPrefix: "ready:", stagePrefix: "stage:", resultPrefix: "result:" },
    },
    release: { requirePreview: true, requireHumanMerge: true, requireQaPass: true },
    qaBrowser: { provider: "playwright", headless: true, artifactsDir: "./artifacts" },
};
export function loadConfig(configPath) {
    const paths = [
        configPath,
        process.env.LINEAR_AGENT_CONFIG,
        "linear-agent.config.json",
        ".linear-agent.json",
        resolve(process.cwd(), "linear-agent.config.json"),
        resolve(process.cwd(), ".linear-agent.json"),
    ].filter(Boolean);
    for (const p of paths) {
        const full = p.startsWith("/") ? p : resolve(process.cwd(), p);
        if (existsSync(full)) {
            try {
                const raw = JSON.parse(readFileSync(full, "utf-8"));
                return ConfigSchema.parse({ ...DEFAULT_CONFIG, ...raw });
            }
            catch (e) {
                console.error(`Failed to load config from ${full}:`, e);
            }
        }
    }
    return DEFAULT_CONFIG;
}
//# sourceMappingURL=config.js.map