import { Redirect } from "expo-router";

/** One-release compatibility route for existing parent links and bookmarks. */
export default function ClassroomRedirect() {
  return <Redirect href="/groups" />;
}
