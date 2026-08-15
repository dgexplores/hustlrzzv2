-- The backend is the only caller of these quota RPCs. Keep them unavailable to
-- anonymous/authenticated browser roles while allowing the server service role.
grant execute on function consume_resume_analysis(uuid, integer) to service_role;
grant execute on function restore_resume_analysis(uuid, boolean) to service_role;
