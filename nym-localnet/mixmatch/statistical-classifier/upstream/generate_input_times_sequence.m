function Si = generate_input_times_sequence(L, lambda)
% Si = generate_input_times_sequence(L, lambda)
%
% It generates a sequence of input times of length L, each separated by an
% independent delay distributed as an exp(lambda) r.v.
  Si = zeros(L, 1);
  Si(1) = get_random_sample('exponential_CDF', lambda, [0, 10/lambda]);
  for k=2:L
    Si(k) = Si(k-1) + get_random_sample('exponential_CDF', lambda, [0, 10/lambda]);
  endfor
endfunction
