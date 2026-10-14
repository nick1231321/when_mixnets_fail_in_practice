function [al_s1, al_s2] = align_sequences(s1, s2, avg_delay)
  l1 = length(s1);
  l2 = length(s2);
  
  min_l = min([l1, l2]);

  delta = 0;
  gap = 64;
  while (gap >= 1.0)
    if delta >= 0
      if l1 <= l2
	if l1 + delta > l2
	  mean_diff = mean(s2((1 + delta):end) - s1(1:(l2 - delta)));
	else
	  mean_diff = mean(s2((1 + delta):(l1 + delta)) - s1);
	endif
      else
	mean_diff = mean(s2((1 + delta):end) - s1(1:(l2 - delta)));
      endif
    else
      if l1 > l2
	if l1 + delta <= l2
	  mean_diff = mean(s2(1:(l1 + delta)) - s1((1 - delta):end));
	else
	  mean_diff = mean(s2 - s1((1 - delta):(l2 - delta)));
	endif
      else
	mean_diff = mean(s2(1:(l1 + delta)) - s1((1 - delta):end));
      endif
    endif
    if mean_diff < avg_delay
      delta = delta + gap;
    else
      delta = delta - gap;
    endif
    gap = gap/2;
  endwhile
  if delta >= 0
    if l1 <= l2
      if l1 + delta > l2
	mean_diff = mean(s2((1 + delta):end) - s1(1:(l2 - delta)));
      else
	mean_diff = mean(s2((1 + delta):(l1 + delta)) - s1);
      endif
    else
      mean_diff = mean(s2((1 + delta):end) - s1(1:(l2 - delta)));
    endif
  else
    if l1 > l2
      if l1 + delta <= l2
	mean_diff = mean(s2(1:(l1 + delta)) - s1((1 - delta):end));
      else
	mean_diff = mean(s2 - s1((1 - delta):(l2 - delta)));
      endif
    else
      mean_diff = mean(s2(1:(l1 + delta)) - s1((1 - delta):end));
    endif
  endif
  % fprintf('Before ''while'' Mean diff = %f\n', mean_diff);
  while mean_diff < avg_delay
    delta = delta + 1;
    if delta >= 0
      if l1 <= l2
	if l1 + delta > l2
	  mean_diff = mean(s2((1 + delta):end) - s1(1:(l2 - delta)));
	else
	  mean_diff = mean(s2((1 + delta):(l1 + delta)) - s1);
	endif
      else
	mean_diff = mean(s2((1 + delta):end) - s1(1:(l2 - delta)));
      endif
    else
      if l1 > l2
	if l1 + delta <= l2
	  mean_diff = mean(s2(1:(l1 + delta)) - s1((1 - delta):end));
	else
	  mean_diff = mean(s2 - s1((1 - delta):(l2 - delta)));
	endif
      else
	mean_diff = mean(s2(1:(l1 + delta)) - s1((1 - delta):end));
      endif
    endif
    % fprintf('Inside ''while'' Mean diff = %f\n', mean_diff);
  endwhile
  if delta >= 0
    if (l1 <= l2)
      if l1 + delta > l2
	al_s1 = s1(1:(l2 - delta));
	al_s2 = s2((1 + delta):end);
      else
	al_s1 = s1;
	al_s2 = s2((1 + delta):(l1 + delta));
      endif
    else
      al_s1 = s1(1:(l2 - delta));
      al_s2 = s2((1 + delta):end);
    endif
  else
    if l1 > l2
      if l1 + delta <= l2
	al_s1 = s1((1 - delta):end);
	al_s2 = s2(1:(l1 + delta));
      else
	al_s1 = s1((1 - delta):(l2 - delta));
	al_s2 = s2;
      endif
    else
      al_s1 = s1((1 - delta):end);
      al_s2 = s2(1:(l1 + delta));
    endif
  endif
endfunction
