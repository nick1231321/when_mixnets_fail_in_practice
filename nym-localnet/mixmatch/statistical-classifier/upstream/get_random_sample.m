function x = get_random_sample(CDF, CDF_parameters, CDF_support)
% x = get_random_sample(CDF, CDF_parameters, CDF_support)
%
% It returns a random sample given a CDF, its parameters, and its support
%
  tolerance = 1e-6;
  CDF_call_suffix = ');';
  for parameter_index = length(CDF_parameters):-1:1
    CDF_call_suffix = [num2str(CDF_parameters(parameter_index)) CDF_call_suffix];
    if parameter_index > 1
      CDF_call_suffix = [', ' CDF_call_suffix];
    endif
  endfor
  min = CDF_support(1);
  max = CDF_support(2);
  uniform_sample = rand();
  diff = 1;
  % fprintf(1, 'Uniform sample value: %f\n', uniform_sample);
  while (abs(diff) > tolerance) && (max - min > tolerance)
    x = (max + min)/2;
    call = ['diff = uniform_sample - ' CDF '(' num2str(x) ', ' CDF_call_suffix];
    % fprintf(1, 'Evaluating %s\n', call);
    eval(call);
    % fprintf(1, 'diff = %f\n', diff);
    if diff > 0
      min = x;
    else
      max = x;
    endif
    % fprintf(1, 'Max - Min = %.3e\n', max - min);
  endwhile
endfunction
