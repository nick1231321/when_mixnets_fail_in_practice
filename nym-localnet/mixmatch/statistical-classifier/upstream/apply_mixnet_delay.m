function So = apply_mixnet_delay(Si, lambda)
% So = apply_mixnet_delay(Si, lambda)
%
% It applies 3 exponential delays to each input time in Si, and returns a
% sorted sequence So of output times
%
  So = zeros(size(Si));
  for index = 1:length(Si)
    So(index) = Si(index) + get_random_sample('erlang_CDF', [5, lambda], [0, 50/lambda]);
  endfor
  So = sort(So);
endfunction
