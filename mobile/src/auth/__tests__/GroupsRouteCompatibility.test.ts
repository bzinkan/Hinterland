import { readFileSync } from "node:fs";
import { resolve } from "node:path";

describe("Groups route compatibility contract", () => {
  it("keeps Groups canonical and redirects the legacy Classroom route", () => {
    const groupsSource = readFileSync(
      resolve(__dirname, "../../../app/groups.tsx"),
      "utf8",
    );
    const classroomSource = readFileSync(
      resolve(__dirname, "../../../app/classroom.tsx"),
      "utf8",
    );
    const staticWebApp = JSON.parse(
      readFileSync(
        resolve(__dirname, "../../../public/staticwebapp.config.json"),
        "utf8",
      ),
    ) as {
      routes: Array<Record<string, unknown>>;
    };

    expect(groupsSource).toContain("Manage your groups");
    expect(classroomSource).toContain('<Redirect href="/groups" />');
    expect(staticWebApp.routes).toContainEqual({
      route: "/classroom",
      methods: ["GET"],
      redirect: "/groups",
      statusCode: 302,
    });
  });
});
