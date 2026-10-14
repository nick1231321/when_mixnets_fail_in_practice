function loglikelihood = sequences_match_loglikelihood(Si, So, lambda)
% loglikelihood = sequences_match_loglikelihood(Si, So, lambda)
%
% It returns the loglikelihood of output sequence So matching input sequence Si.
%
% It performs an exahustive recursive search with cut-off.
%
  assert(length(Si) == length(So));
  cutoff_loglikelihood = -7;
% fprintf(1, 'Length of sequences: %i\n', length(Si));
  if length(Si) < 1
    loglikelihood = 0;
    return;
  endif
% We pick the first output time in So and select all possible matching times from Si
  to = So(1);
  possible_input_times = Si(Si < to);
  P = 0;
  remaining_So = So(2:end);
  for input_time_index = 1:length(possible_input_times)
    ti = possible_input_times(input_time_index);
    remaining_Si = setdiff(Si, ti);
    L_ti_to = log(erlang_pdf(to - ti, 3, lambda));
    if L_ti_to < -20
      continue;
    else
      P = P + exp(L_ti_to + sequences_match_loglikelihood(remaining_Si, remaining_So, lambda));
    endif
  endfor
  loglikelihood = log(P);
endfunction
