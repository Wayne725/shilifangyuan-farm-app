import { ApiError } from "./api";
import type { Meeting, MeetingCategory } from "./types";

export function meetingCategoryName(meeting: Meeting) {
  return meeting.category_name || ({ general_assembly: "社員大會", affairs: "社務會議" }[meeting.meeting_type]) || "未分類";
}

export function meetingCategoryOptions(meetings: Meeting[], categories: MeetingCategory[] = []) {
  const options = new Map(categories.map(category => [category.id, category.name]));
  for (const meeting of meetings) {
    const id = meeting.category_id || meeting.meeting_type;
    if (!options.has(id)) options.set(id, meetingCategoryName(meeting));
  }
  return [...options].map(([id, name]) => ({ id, name }));
}

export function filterMeetings(meetings: Meeting[], categoryId: string, keyword: string) {
  const search = keyword.trim().toLocaleLowerCase();
  return meetings.filter(meeting => (!categoryId || (meeting.category_id || meeting.meeting_type) === categoryId)
    && [meeting.title, meeting.location, meetingCategoryName(meeting),
      ...meeting.agenda.map(item => item.title || item.label || item.subject || ""),
      ...meeting.resolutions.flatMap(item => [item.title, item.resolution_text])]
      .join(" ").toLocaleLowerCase().includes(search));
}

export function meetingAccessDenied(error: unknown) {
  return error instanceof ApiError && [401, 403].includes(error.status);
}

export function retryMeetingRead(failureCount: number, error: Error) {
  return !meetingAccessDenied(error) && failureCount < 1;
}
